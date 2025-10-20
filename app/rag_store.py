import os, uuid
import chromadb
from chromadb.config import Settings
from pypdf import PdfReader
from tiktoken import get_encoding
from typing import List, Tuple

ENC = get_encoding("cl100k_base")

def _chunks(text: str, max_tokens: int = 650, overlap: int = 60):
    tokens = ENC.encode(text or "")
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        yield ENC.decode(tokens[start:end])
        start = end - overlap if end - overlap > 0 else end

def load_pdf_text(path: str) -> str:
    reader = PdfReader(path)
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)

def get_db(persist_dir: str = "/data/chroma"):
    os.makedirs(persist_dir, exist_ok=True)
    client = chromadb.PersistentClient(path=persist_dir, settings=Settings(allow_reset=False))
    return client.get_or_create_collection("licitabot_docs")

def ingest_paths(paths: List[str]) -> int:
    col = get_db()
    ids, docs, meta = [], [], []
    for p in paths:
        try:
            raw = load_pdf_text(p)
        except Exception:
            continue
        base = os.path.basename(p)
        i = 0
        for ch in _chunks(raw):
            ids.append(str(uuid.uuid4()))
            docs.append(ch)
            meta.append({"source": base, "chunk": i})
            i += 1
    if ids:
        col.add(ids=ids, documents=docs, metadatas=meta)
    return len(paths)

def search(query: str, k: int = 4) -> List[Tuple[str, dict]]:
    col = get_db()
    res = col.query(query_texts=[query], n_results=k)
    hits = []
    if res and res.get("documents"):
        for doc, md in zip(res["documents"][0], res["metadatas"][0]):
            hits.append((doc, md))
    return hits

def context_from_hits(hits: List[Tuple[str, dict]]) -> str:
    if not hits:
        return "Nenhum trecho encontrado."
    blocks = []
    for doc, md in hits:
        blocks.append(f"[{md.get('source')} - parte {md.get('chunk')}] {doc}")
    return "\n\n".join(blocks)
