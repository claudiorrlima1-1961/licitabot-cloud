# -*- coding: utf-8 -*-
import os, uuid
from typing import List, Tuple
import chromadb
from chromadb.config import Settings
from pypdf import PdfReader
from tiktoken import get_encoding

ENC = get_encoding("cl100k_base")

# Diretório persistente (Render usa /data/chroma)
PERSIST_DIR = "/data/chroma"

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

    for i, page in enumerate(reader.pages):
        txt = (page.extract_text() or "").strip()
        if not txt:
            sem_texto += 1
        partes.append(txt)

    if total > 0 and sem_texto / total >= 0.8:
        print(f"[AVISO] {os.path.basename(path)} parece ser PDF de imagem ({sem_texto}/{total} páginas sem texto).")
        return ""

    texto = "\n".join([p for p in partes if p])
    print(f"[OK] Extraído texto de {os.path.basename(path)} ({sem_texto}/{total} páginas sem texto).")
    return texto

def get_db():
    os.makedirs(PERSIST_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=PERSIST_DIR, settings=Settings(allow_reset=False))
    return client.get_or_create_collection("licitabot_docs")

def ingest_paths(paths: List[str]) -> int:
    """Indexa PDFs válidos (com texto)."""
    col = get_db()
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
            ids.append(str(uuid.uuid4()))
            docs.append(ch)
            meta.append({"source": base, "chunk": i})

    if ids:
        col.add(ids=ids, documents=docs, metadatas=meta)
        print(f"[INDEX] {len(ids)} blocos adicionados ({len(paths)} arquivo[s]).")
    else:
        print("[INDEX] Nenhum texto adicionado — possivelmente PDFs-imagem.")
    return len(ids)

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
    partes = [f"[{md.get('source')} - parte {md.get('chunk')}] {doc}" for doc, md in hits]
    return "\n\n".join(partes)
