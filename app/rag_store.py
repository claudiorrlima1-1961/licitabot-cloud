# app/rag_store.py
# Camada RAG: leitura de PDFs, extração de texto (inclui OCR para PDF imagem),
# chunking, indexação no ChromaDB persistente e busca.

import os
import uuid
from typing import List, Tuple

import chromadb
from chromadb.config import Settings
from pypdf import PdfReader

# Vamos tentar OCR (tesseract) quando a página não tiver texto extraível.
# OBS: isso só vai funcionar em produção se o container tiver tesseract + poppler.
try:
    from pdf2image import convert_from_path
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

import tiktoken
ENC = tiktoken.get_encoding("cl100k_base")

###############################################################################
# 1. Utilidades de chunk
###############################################################################

def _chunk_text(text: str, max_tokens: int = 650, overlap: int = 60):
    """
    Quebra o texto grande em pedaços (~650 tokens) com sobreposição (~60 tokens)
    para dar contexto nas buscas.
    """
    tokens = ENC.encode(text or "")
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        piece_tokens = tokens[start:end]
        yield ENC.decode(piece_tokens)
        # anda pro próximo bloco com sobreposição
        start = end - overlap if end - overlap > 0 else end

###############################################################################
# 2. Extração de texto de PDF (inclui fallback OCR)
###############################################################################

def _extract_page_text(page) -> str:
    """Tenta extrair texto 'normal' da página PDF."""
    try:
        txt = page.extract_text() or ""
    except Exception:
        txt = ""
    return txt.strip()

def _extract_pdf_text_plain(pdf_path: str) -> List[str]:
    """
    Extrai texto 'normal' página a página com pypdf.
    Retorna lista de textos por página.
    """
    pages_text = []
    reader = PdfReader(pdf_path)
    for page in reader.pages:
        pages_text.append(_extract_page_text(page))
    return pages_text

def _extract_pdf_text_ocr(pdf_path: str) -> List[str]:
    """
    Fallback OCR:
    - Converte cada página do PDF em imagem
    - Roda pytesseract em cima da imagem
    - Retorna lista de textos por página
    Se OCR não estiver disponível no container, devolve lista vazia.
    """
    if not OCR_AVAILABLE:
        return []

    pages_text = []
    try:
        images = convert_from_path(pdf_path)  # precisa do poppler no container
        for img in images:
            ocr_txt = pytesseract.image_to_string(img) or ""
            pages_text.append(ocr_txt.strip())
    except Exception:
        # Se der erro (PDF grande demais, falta lib, etc.), apenas retorna vazio
        return []
    return pages_text

def load_pdf_text(pdf_path: str) -> str:
    """
    Lê um PDF e retorna TODO o texto concatenado.
    Passos:
    1. tenta extrair texto "digital" (pypdf)
    2. se quase tudo vier vazio, tenta OCR
    """
    # Passo 1: texto normal
    plain_pages = _extract_pdf_text_plain(pdf_path)

    # Heurística: se 80%+ das páginas vieram vazias, tentamos OCR
    if plain_pages:
        non_empty = sum(1 for t in plain_pages if t.strip())
        empty_ratio = 1 - (non_empty / len(plain_pages))
    else:
        empty_ratio = 1.0

    if empty_ratio > 0.8:
        # Quase tudo vazio -> tenta OCR
        ocr_pages = _extract_pdf_text_ocr(pdf_path)
        if ocr_pages:
            pages_to_use = ocr_pages
        else:
            pages_to_use = plain_pages  # fallback, mesmo vazio
    else:
        pages_to_use = plain_pages

    # Junta tudo num texto só
    return "\n\n".join(pages_to_use)

###############################################################################
# 3. Banco vetorial (ChromaDB) persistente
###############################################################################

def _get_chroma(persist_dir: str = "/data/chroma"):
    """
    Garante que temos um diretório persistente para o índice vetorial no Render.
    Em desenvolvimento local sem /data, cai para ./chroma_local dentro do repo.
    """
    if not os.path.isdir("/data"):
        persist_dir = os.path.join(os.path.dirname(__file__), "chroma_local")

    os.makedirs(persist_dir, exist_ok=True)

    client = chromadb.PersistentClient(
        path=persist_dir,
        settings=Settings(allow_reset=False)
    )

    col = client.get_or_create_collection("licitabot_docs")
    return col

###############################################################################
# 4. Indexação
###############################################################################

def ingest_paths(paths: List[str]) -> int:
    """
    Recebe uma lista de caminhos de PDF.
    Extrai texto, faz chunk e grava cada chunk no ChromaDB com metadados:
      - 'source' (nome do arquivo)
      - 'chunk' (número do pedaço)
    Retorna quantos ARQUIVOS foram processados.
    """
    col = _get_chroma()

    for pdf_path in paths:
        if not os.path.isfile(pdf_path):
            continue

        base_name = os.path.basename(pdf_path)

        try:
            full_text = load_pdf_text(pdf_path)
        except Exception:
            # Se nem conseguimos ler o PDF, pula
            continue

        if not full_text.strip():
            # PDF vazio (nem OCR ajudou)
            continue

        # Quebrar esse PDF em blocos
        docs = []
        metas = []
        ids = []

        chunk_id = 0
        for piece in _chunk_text(full_text):
            ids.append(str(uuid.uuid4()))
            docs.append(piece)
            metas.append({"source": base_name, "chunk": chunk_id})
            chunk_id += 1

        if ids:
            col.add(
                ids=ids,
                documents=docs,
                metadatas=metas
            )

    return len(paths)

###############################################################################
# 5. Busca
###############################################################################

def search(query: str, k: int = 4) -> List[Tuple[str, dict]]:
    """
    Faz busca semântica no índice.
    Retorna lista de tuplas (trecho_do_documento, metadados).
    """
    col = _get_chroma()
    res = col.query(query_texts=[query], n_results=k)

    hits: List[Tuple[str, dict]] = []
    if res and res.get("documents"):
        for doc, md in zip(res["documents"][0], res["metadatas"][0]):
            hits.append((doc, md))
    return hits

def context_from_hits(hits: List[Tuple[str, dict]]) -> str:
    """
    Monta um contexto legível para mandar pro modelo responder.
    Inclui o nome do PDF e o número do pedaço.
    """
    if not hits:
        return "Nenhum trecho encontrado."

    blocos = []
    for doc, md in hits:
        blocos.append(
            f"[{md.get('source')} - parte {md.get('chunk')}] {doc}"
        )
    return "\n\n".join(blocos)
