# -*- coding: utf-8 -*-
import os, uuid
from typing import List, Tuple
from pypdf import PdfReader
from tiktoken import get_encoding

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

ENC = get_encoding("cl100k_base")

# -------------------- Diretório persistente do índice ------------------------
# Prioridade: CHROMA_DIR (env) -> /data/chroma (se existir) -> ./app/chroma
_DEF_DATA = "/data/chroma" if os.path.isdir("/data") else os.path.join(os.path.dirname(__file__), "chroma")
PERSIST_DIR = os.getenv("CHROMA_DIR", _DEF_DATA)
os.makedirs(PERSIST_DIR, exist_ok=True)

# -------------------- Embeddings explícitos ----------------------------------
# Modelo leve e eficiente; melhora muito a qualidade das buscas sem termo exato
EMB = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")

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
    # Observação: definir embedding_function AQUI é essencial para a query retornar resultados
    return client.get_or_create_collection(
        name="licitabot_docs",
        embedding_function=EMB,
        metadata={"hnsw:space": "cosine"}
    )

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

    # Para evitar conflito de IDs já existentes, removemos e re-adicionamos (idempotente)
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
