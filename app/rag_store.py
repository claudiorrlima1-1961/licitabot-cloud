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
        yield ENC.decode(tokens[start
