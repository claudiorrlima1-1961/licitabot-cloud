import os, uuid, logging
import chromadb
from chromadb.config import Settings
from pypdf import PdfReader
from tiktoken import get_encoding
from typing import List, Tuple

ENC = get_encoding("cl100k_base")
log = logging.getLogger("licitabot.rag")

def _chunks(text: str, max_tokens: int = 650, overlap: int = 60):
    tokens = ENC.encode(text or "")
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        yield ENC.decode(tokens[start:end])
        start = end - overlap if end - overlap > 0 else end

# --------- OCR opcional (usa se disponível) ----------
def _try_ocr_pdf(path: str) -> str:
    """
    Tenta extrair texto via OCR.
    Requer 'pytesseract' + 'pdf2image' (+ poppler instalado no sistema).
    Se não estiverem disponíveis, retorna string vazia.
    """
    try:
        import pytesseract
        from pdf2image import convert_from_path
        from PIL import Image

        poppler_path = os.getenv("POPPLER_PATH")  # se você setar isso no Render
        pages = convert_from_path(path, dpi=200, poppler_path=poppler_path)  # pode demorar
        texts = []
        for img in pages:
            if not isinstance(img, Image.Image):
                img = img.convert("RGB")
            txt = pytesseract.image_to_string(img, lang=os.getenv("TESSERACT_LANG", "por"))
            texts.append(txt or "")
        ocr_text = "\n".join(texts).strip()
        if ocr_text:
            log.info(f"✅ OCR gerou {len(ocr_text)} chars para {os.path.basename(path)}")
        else:
            log.warning(f"OCR não retornou texto para {os.path.basename(path)}")
        return ocr_text
    except Exception as e:
        log.warning(f"OCR indisponível/erro ({type(e).__name__}): {e}")
        return ""

def _extract_pdf_text(path: str) -> str:
    """Extrai com pypdf; se vier vazio, tenta OCR opcional."""
    try:
        reader = PdfReader(path)
        parts = []
        empty_pages = 0
        for page in reader.pages:
            t = page.extract_text() or ""
            if not t.strip():
                empty_pages += 1
            parts.append(t)
        raw = "\n".join(parts).strip()
        if raw:
            return raw
        # Parece PDF-imagem? tenta OCR:
        if empty_pages == len(reader.pages):
            log.info(f"PDF parece imagem. Tentando OCR: {os.path.basename(path)}")
            return _try_ocr_pdf(path)
        return raw
    except Exception as e:
        log.warning(f"Falha pypdf ({type(e).__name__}): {e}. Tentando OCR...")
        return _try_ocr_pdf(path)

def get_db(persist_dir: str = "/data/chroma"):
    os.makedirs(persist_dir, exist_ok=True)
    client = chromadb.PersistentClient(path=persist_dir, settings=Settings(allow_reset=False))
    return client.get_or_create_collection("licitabot_docs")

def ingest_paths(paths: List[str]) -> int:
    col = get_db()
    total_docs = 0
    for p in paths:
        base = os.path.basename(p)
        text = _extract_pdf_text(p)
        if not text:
            log.warning(f"⚠️ Sem texto extraível: {base}")
            continue

        ids, docs, meta = [], [], []
        i = 0
        for ch in _chunks(text):
            ids.append(str(uuid.uuid4()))
            docs.append(ch)
            meta.append({"source": base, "chunk": i})
            i += 1

        if ids:
            col.add(ids=ids, documents=docs, metadatas=meta)
            total_docs += 1
            log.info(f"📥 Indexado: {base} ({len(ids)} pedaços)")
    return total_docs

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
