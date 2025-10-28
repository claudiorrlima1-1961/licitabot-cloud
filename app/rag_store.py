# app/rag_store.py
import os
import uuid
from typing import List, Tuple

import chromadb
from chromadb.config import Settings
from pypdf import PdfReader
from tiktoken import get_encoding

ENC = get_encoding("cl100k_base")


def _chunks(text: str, max_tokens: int = 650, overlap: int = 60):
    """Quebra o texto em janelas de tokens com sobreposição."""
    tokens = ENC.encode(text or "")
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        yield ENC.decode(tokens[start:end])
        start = end - overlap if end - overlap > 0 else end


def load_pdf_text(path: str) -> str:
    """Extrai texto de todas as páginas do PDF (apenas PDFs textuais)."""
    reader = PdfReader(path)
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _resolve_chroma_dir() -> str:
    """
    Usa /data/chroma (Render com Disco) se existir /data;
    caso contrário, salva em app/chroma (execução local).
    """
    if os.path.isdir("/data"):
        base_dir = "/data/chroma"
    else:
        base_dir = os.path.join(os.path.dirname(__file__), "chroma")

    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def get_db():
    """
    Retorna cliente ChromaDB persistente com collection 'licitabot_docs'.
    """
    base_dir = _resolve_chroma_dir()
    client = chromadb.PersistentClient(path=base_dir, settings=Settings(allow_reset=False))
    return client.get_or_create_collection("licitabot_docs")


def ingest_paths(paths: List[str]) -> int:
    """
    Ingere (indexa) uma lista de caminhos de PDF.
    Retorna o total de CHUNKS adicionados (não o número de arquivos).
    """
    col = get_db()
    total_chunks = 0

    for p in paths:
        try:
            raw = load_pdf_text(p)
            if not raw or not raw.strip():
                print(f"[WARN] PDF sem texto (ignorado): {p}")
                continue
        except Exception as e:
            print(f"[ERRO] Falha ao ler PDF '{p}': {type(e).__name__} - {e}")
            continue

        base = os.path.basename(p)
        docs, ids, meta = [], [], []

        for i, ch in enumerate(_chunks(raw)):
            ids.append(str(uuid.uuid4()))
            docs.append(ch)
            meta.append({"source": base, "chunk": i})

        if docs:
            col.add(ids=ids, documents=docs, metadatas=meta)
            total_chunks += len(docs)
            print(f"[OK] Indexado {len(docs)} trechos de '{base}'")

    print(f"[RESUMO] Total de trechos adicionados: {total_chunks}")
    return total_chunks


def search(query: str, k: int = 4) -> List[Tuple[str, dict]]:
    """
    Faz a busca semântica e retorna lista de (trecho, metadados).
    """
    col = get_db()
    res = col.query(query_texts=[query], n_results=k)
    hits = []
    if res and res.get("documents"):
        docs = res["documents"][0] or []
        metas = res["metadatas"][0] or []
        for doc, md in zip(docs, metas):
            hits.append((doc, md))
    return hits


def context_from_hits(hits: List[Tuple[str, dict]]) -> str:
    """
    Monta um contexto textual agregando os melhores trechos, com fonte e parte.
    """
    if not hits:
        return "Nenhum trecho encontrado."
    blocks = []
    for doc, md in hits:
        blocks.append(f"[{md.get('source')} - parte {md.get('chunk')}] {doc}")
    return "\n\n".join(blocks)
