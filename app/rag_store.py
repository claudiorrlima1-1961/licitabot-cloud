# -*- coding: utf-8 -*-
import os, uuid, io
from typing import List, Tuple

import chromadb
from chromadb.config import Settings
from pypdf import PdfReader
from tiktoken import get_encoding

# OCR (carregado de forma opcional; se não estiver instalado, seguimos sem OCR)
try:
    import pytesseract
    from pdf2image import convert_from_path
    _OCR_AVAILABLE = True
except Exception:
    pytesseract = None
    convert_from_path = None
    _OCR_AVAILABLE = False

ENC = get_encoding("cl100k_base")

# --------- CHUNKING -----------------------------------------------------------
def _chunks(text: str, max_tokens: int = 650, overlap: int = 60):
    tokens = ENC.encode(text or "")
    n = len(tokens)
    i = 0
    while i < n:
        j = min(i + max_tokens, n)
        yield ENC.decode(tokens[i:j])
        i = j - overlap if j - overlap > 0 else j

# --------- EXTRAÇÃO DE TEXTO --------------------------------------------------
def _extract_text_with_pypdf(path: str) -> str:
    try:
        reader = PdfReader(path)
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts
