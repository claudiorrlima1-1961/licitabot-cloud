# -*- coding: utf-8 -*-
import os, uuid, json, time
from typing import List, Tuple, Optional
from pypdf import PdfReader
from tiktoken import get_encoding

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

# ---------- Config básica ----------
ENC = get_encoding("cl100k_base")

# Diretório persistente do índice (compatível com Render)
_DEF_DATA = "/data/chroma" if os.path.isdir("/data") else os.path.join(os.path.dirname(__file__), "chroma")
PERSIST_DIR = os.getenv("CHROMA_DIR", _DEF_DATA)
os.makedirs(PERSIST_DIR, exist_ok=True)

# ---------- Seleção de Embeddings (Render-friendly) ----------
# Preferência: Azure OpenAI -> OpenAI -> (opcional) SentenceTransformers se já existir
def _azure_embedder():
    import requests  # nativo no Render; sem SDK pesado
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")
    if not (endpoint and api_key and deployment):
        return None

    class AzureOpenAIEmbedding:
        def __call__(self, texts: List[str]) -> List[List[float]]:
            url = f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version=2023-05-15"
            headers = {"Content-Type": "application/json", "api-key": api_key}
            r = requests.post(url, headers=headers, data=json.dumps({"input": texts}), timeout=60)
            r.raise_for_status()
            data = r.json()
            return [item["embedding"] for item in data["data"]]
    print("[EMB] Usando Azure OpenAI embeddings")
    return AzureOpenAIEmbedding()

def _openai_embedder():
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    print("[EMB] Usando OpenAI embeddings")
    return OpenAIEmbeddingFunction(api_key=api_key, model_name=model)

def _st_embedder():
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        model = os.getenv("ST_MODEL", "all-MiniLM-L6-v2")
        print("[EMB] Usando SentenceTransformers (se modelo já existir no filesystem)")
        return SentenceTransformerEmbeddingFunction(model_name=model)
    except Exception:
        return None

EmbeddingFn = _azure_embedder() or _openai_embedder() or _st_embedder()
if EmbeddingFn is None:
    raise RuntimeError(
        "Nenhuma fonte de embeddings disponível. Configure:\n"
        "- Azure OpenAI: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_EMBEDDING_DEPLOYMENT\n"
        "- ou OPENAI_API_KEY (e opcional OPENAI_EMBEDDING_MODEL)\n"
        "- (opcional) SentenceTransformers se o modelo já estiver no container."
    )

# ---------- OCR opcional ----------
def _ocr_with_azure_read(pdf_path: str) -> Optional[str]:
    """OCR via Azure Vision Read (v3.2). Requer AZURE_VISION_ENDPOINT e AZURE_VISION_KEY."""
    endpoint = os.getenv("AZURE_VISION_ENDPOINT", "").rstrip("/")
    key = os.getenv("AZURE_VISION_KEY", "")
    if not (endpoint and key):
        return None

    import requests

    analyze_url = f"{endpoint}/vision/v3.2/read/analyze"
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/pdf"}
    try:
        with open(pdf_path, "rb") as f:
            r = requests.post(analyze_url, headers=headers, data=f.read(), timeout=60)
        if r.status_code not in (202, 200):
            print(f"[OCR-AZURE] Falha ao enviar PDF: {r.status_code} {r.text[:180]}")
            return None
        op_location = r.headers.get("Operation-Location")
        if not op_location:
            print("[OCR-AZURE] Operation-Location ausente")
            return None

        # poll até concluir
        for _ in range(40):
            time.sleep(1.0)
            rr = requests.get(op_location, headers={"Ocp-Apim-Subscription-Key": key}, timeout=30)
            rr.raise_for_status()
            data = rr.json()
            status = data.get("status") or data.get("statusCode")
            if (data.get("status") == "succeeded") or (isinstance(status, str) and status.lower() == "succeeded"):
                # Formato v3.2:
                read_res = data.get("analyzeResult", {}).get("readResults", [])
                parts = []
                for page in read_res:
                    for line in page.get("lines", []):
                        parts.append(line.get("text", ""))
                text = "\n".join(parts).strip()
                print(f"[OCR-AZURE] OCR concluído ({len(text)} chars)")
                return text
            if (data.get("status") == "failed") or (isinstance(status, str) and status.lower() == "failed"):
                print("[OCR-AZURE] OCR retornou failed")
                return None
        print("[OCR-AZURE] Timeout aguardando OCR")
        return None
    except Exception as e:
        print(f"[OCR-AZURE] Erro: {type(e).__name__}: {e}")
        return None

def _extract_with_pymupdf(pdf_path: str) -> Optional[str]:
    """Fallback para PyMuPDF (se instalado). NÃO faz OCR, mas extrai texto melhor que pypdf."""
    try:
        import fitz  # PyMuPDF
    except Exception:
        return None
    try:
        doc = fitz.open(pdf_path)
        parts = []
        for pg in doc:
            parts.append(pg.get_text().strip())
        text = "\n".join([p for p in parts if p]).strip()
        print(f"[PyMuPDF] Extraído {len(text)} chars")
        return text or None
    except Exception as e:
        print(f"[PyMuPDF] Erro: {e}")
        return None

# ---------- Utilidades ----------
def _chunks(text: str, max_tokens: int = 650, overlap: int = 60):
    tokens = ENC.encode(text or "")
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        yield ENC.decode(tokens[start:end])
        start = end - overlap if end - overlap > 0 else end

def load_pdf_text(path: str) -> str:
    """Extrai texto de PDF. Tenta pypdf -> PyMuPDF -> Azure OCR (se sem texto)."""
    # 1) pypdf (rápido)
    try:
        reader = PdfReader(path)
        total = len(reader.pages)
        sem_texto = 0
        partes = []
        for page in reader.pages:
            txt = (page.extract_text() or "").strip()
            if not txt:
                sem_texto += 1
            partes.append(txt)
        texto = "\n".join([p for p in partes if p]).strip()
        if texto and not (total > 0 and sem_texto / total >= 0.8):
            print(f"[pypdf] Extraído {len(texto)} chars | {sem_texto}/{total} páginas sem texto")
            return texto
        print(f"[pypdf] Parece imagem ({sem_texto}/{total} sem texto); tentando fallbacks…")
    except Exception as e:
        print(f"[pypdf] Falha ao abrir {path}: {e}")

    # 2) PyMuPDF (se disponível)
    text2 = _extract_with_pymupdf(path)
    if text2 and len(text2) >= 40:  # texto mínimo para valer
        return text2

    # 3) Azure OCR (se configurado)
    text3 = _ocr_with_azure_read(path)
    if text3 and len(text3) >= 20:
        return text3

    print(f"[AVISO] {os.path.basename(path)} sem texto utilizável (precisa de OCR).")
    return ""

def _get_collection():
    client = chromadb.PersistentClient(path=PERSIST_DIR, settings=Settings(allow_reset=False))
    col = client.get_or_create_collection(
        name="licitabot_docs",
        embedding_function=EmbeddingFn,
        metadata={"hnsw:space": "cosine"}
    )
    print(f"[CHROMA] Collection pronta em {PERSIST_DIR}")
    return col

# ---------- API pública ----------
def ingest_paths(paths: List[str]) -> int:
    """Indexa PDFs válidos (com texto). Retorna número de chunks adicionados."""
    col = _get_collection()
    ids, docs, meta = [], [], []

    for p in paths:
        if not os.path.exists(p):
            print(f"[ERRO] Arquivo não encontrado: {p}")
            continue

        raw = load_pdf_text(p)
        if not raw.strip():
            continue

        base = os.path.basename(p)
        for i, ch in enumerate(_chunks(raw)):
            stable_id = f"{base}::part-{i}"              # evita duplicação
            ids.append(stable_id)
            docs.append(ch)
            meta.append({"source": base, "chunk": i, "path": os.path.abspath(p)})

    if not ids:
        print("[INDEX] Nenhum texto adicionado (talvez PDFs-imagem sem OCR).")
        return 0

    # Reindex idempotente
    try:
        col.delete(ids=ids)
    except Exception:
        pass

    col.add(ids=ids, documents=docs, metadatas=meta)
    print(f"[INDEX] {len(ids)} blocos adicionados de {len(paths)} arquivo(s). Dir índice: {PERSIST_DIR}")
    return len(ids)

def search(query: str, k: int = 4) -> List[Tuple[str, dict]]:
    col = _get_collection()
    res = col.query(query_texts=[query], n_results=max(1, k))
    hits: List[Tuple[str, dict]] = []
    if res and res.get("documents"):
        for doc, md in zip(res["documents"][0], res["metadatas"][0]):
            hits.append((doc, md))
    print(f"[PESQUISA] q='{query}' -> {len(hits)} hit(s).")
    return hits

def context_from_hits(hits: List[Tuple[str, dict]]) -> str:
    if not hits:
        return "Nenhum trecho encontrado."
    partes = [f"[{md.get('source')} - parte {md.get('chunk')}] {doc}" for doc, md in hits]
    return "\n\n".join(partes)
