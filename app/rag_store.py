# -*- coding: utf-8 -*-
import os, uuid, json
from typing import List, Tuple
from pypdf import PdfReader
from tiktoken import get_encoding

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

ENC = get_encoding("cl100k_base")

# -------------------- Diretório persistente do índice ------------------------
_DEF_DATA = "/data/chroma" if os.path.isdir("/data") else os.path.join(os.path.dirname(__file__), "chroma")
PERSIST_DIR = os.getenv("CHROMA_DIR", _DEF_DATA)
os.makedirs(PERSIST_DIR, exist_ok=True)

# -------------------- Seleção dinâmica de Embeddings -------------------------
# Ordem de preferência:
# 1) Azure OpenAI (AZURE_OPENAI_* vars)
# 2) OpenAI (OPENAI_API_KEY)
# 3) SentenceTransformers (se estiver instalado)
#
# A interface esperada por Chroma é uma função/objeto com método __call__(self, texts: List[str]) -> List[List[float]]
EmbeddingFn = None
EMB_DESC = ""

def _azure_embedder():
    """Cria um embedder para Azure OpenAI via requests (sem depender de SDK)."""
    import requests

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")  # ex: 'text-embedding-3-small'

    if not (endpoint and api_key and deployment):
        return None

    class AzureOpenAIEmbedding:
        def __call__(self, texts: List[str]) -> List[List[float]]:
            url = f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version=2023-05-15"
            headers = {
                "Content-Type": "application/json",
                "api-key": api_key,
            }
            data = {"input": texts}
            r = requests.post(url, headers=headers, data=json.dumps(data), timeout=60)
            r.raise_for_status()
            out = r.json()
            # compat: alguns retornos usam 'data' com dicts contendo 'embedding'
            return [item["embedding"] for item in out["data"]]

    return AzureOpenAIEmbedding()

def _openai_embedder():
    """Cria o embedder nativo do Chroma para OpenAI, se OPENAI_API_KEY existir."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    return OpenAIEmbeddingFunction(api_key=api_key, model_name=model)

def _sentence_transformers_embedder():
    """Usa sentence-transformers apenas se já estiver instalado (sem downloads pesados)."""
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except Exception:
        return None
    model = os.getenv("ST_MODEL", "all-MiniLM-L6-v2")
    try:
        return SentenceTransformerEmbeddingFunction(model_name=model)
    except Exception:
        # se o modelo não estiver disponível localmente, evitar baixar no Render
        return None

# Resolver embedder
EmbeddingFn = _azure_embedder()
EMB_DESC = "Azure OpenAI" if EmbeddingFn else ""

if EmbeddingFn is None:
    EmbeddingFn = _openai_embedder()
    EMB_DESC = "OpenAI" if EmbeddingFn else EMB_DESC

if EmbeddingFn is None:
    EmbeddingFn = _sentence_transformers_embedder()
    EMB_DESC = "SentenceTransformers" if EmbeddingFn else EMB_DESC

if EmbeddingFn is None:
    raise RuntimeError(
        "Nenhuma fonte de embeddings encontrada.\n"
        "Configure uma das opções:\n"
        "  - Azure OpenAI: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_EMBEDDING_DEPLOYMENT\n"
        "  - OpenAI: OPENAI_API_KEY (opcional OPENAI_EMBEDDING_MODEL)\n"
        "  - Ou instale sentence-transformers e disponibilize o modelo localmente."
    )

# -------------------- Utilidades ---------------------------------------------
def _chunks(text: str, max_tokens: int = 650, overlap: int = 60):
    tokens = ENC.encode(text or "")
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        yield ENC.decode(tokens[start:end])
        start = end - overlap if end - overlap > 0 else end

def load_pdf_text(path: str) -> str:
    """Extrai texto de PDF; ignora PDFs-imagem (escaneados)."""
    try:
        reader = PdfReader(path)
    except Exception as e:
        print(f"[ERRO] Falha ao abrir {path}: {e}")
        return ""

    total = len(reader.pages)
    sem_texto = 0
    partes = []

    for page in reader.pages:
        txt = (page.extract_text() or "").strip()
        if not txt:
            sem_texto += 1
        partes.append(txt)

    if total > 0 and sem_texto / total >= 0.8:
        print(f"[AVISO] {os.path.basename(path)} parece ser PDF de imagem ({sem_texto}/{total} sem texto).")
        return ""

    texto = "\n".join([p for p in partes if p])
    print(f"[OK] Extraído texto de {os.path.basename(path)} ({sem_texto}/{total} páginas sem texto).")
    return texto

def _get_collection():
    client = chromadb.PersistentClient(path=PERSIST_DIR, settings=Settings(allow_reset=False))
    col = client.get_or_create_collection(
        name="licitabot_docs",
        embedding_function=EmbeddingFn,
        metadata={"hnsw:space": "cosine"}
    )
    print(f"[CHROMA] Collection pronta em {PERSIST_DIR} | Embeddings: {EMB_DESC}")
    return col

# -------------------- API pública --------------------------------------------
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
            # ID determinístico evita duplicar caso o mesmo arquivo seja ingerido novamente
            stable_id = f"{base}::part-{i}"
            ids.append(stable_id)
            docs.append(ch)
            meta.append({"source": base, "chunk": i, "path": os.path.abspath(p)})

    if not ids:
        print("[INDEX] Nenhum texto adicionado — possivelmente PDFs-imagem ou vazios.")
        return 0

    # remove IDs se já existirem (idempotente) e adiciona novamente
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
    print(f"[SEARCH] q='{query}' -> {len(hits)} hit(s).")
    return hits

def context_from_hits(hits: List[Tuple[str, dict]]) -> str:
    if not hits:
        return "Nenhum trecho encontrado."
    partes = [f"[{md.get('source')} - parte {md.get('chunk')}] {doc}" for doc, md in hits]
    return "\n\n".join(partes)
