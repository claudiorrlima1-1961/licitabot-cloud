# -*- coding: utf-8 -*-
import os
import secrets
import logging
from typing import Optional, Dict
from datetime import datetime, timedelta

from fastapi import (
    FastAPI, Request, UploadFile, File, Header, HTTPException,
    APIRouter, BackgroundTasks, Depends
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse

# ==================== CONFIGURAÇÃO PROFISSIONAL ====================
app = FastAPI(title="Licitabot – Sistema Premium")

# Configuração de logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("licitabot")

# ==================== BANCO DE USUÁRIOS ====================
# Em produção, use um banco de dados real
USERS_DB = {
    "admin": {
        "password": os.getenv("ADMIN_PASSWORD", "admin123"),
        "role": "admin",
        "name": "Administrador"
    },
    "cliente": {
        "password": os.getenv("CLIENT_PASSWORD", "cliente123"), 
        "role": "user",
        "name": "Cliente Premium"
    }
}

# ==================== SISTEMA DE SESSÕES ====================
active_sessions: Dict[str, dict] = {}

def create_session(username: str) -> str:
    """Cria uma sessão segura"""
    session_id = secrets.token_urlsafe(32)
    active_sessions[session_id] = {
        "username": username,
        "role": USERS_DB[username]["role"],
        "name": USERS_DB[username]["name"],
        "created_at": datetime.now(),
        "last_activity": datetime.now()
    }
    return session_id

def verify_session(session_id: str) -> Optional[dict]:
    """Verifica se a sessão é válida"""
    if session_id not in active_sessions:
        return None
    
    session = active_sessions[session_id]
    
    # Verifica se a sessão expirou (24 horas)
    if datetime.now() - session["created_at"] > timedelta(hours=24):
        del active_sessions[session_id]
        return None
    
    # Atualiza última atividade
    session["last_activity"] = datetime.now()
    return session

def cleanup_sessions():
    """Limpa sessões expiradas"""
    expired = []
    for session_id, session in active_sessions.items():
        if datetime.now() - session["last_activity"] > timedelta(hours=24):
            expired.append(session_id)
    
    for session_id in expired:
        del active_sessions[session_id]

# ==================== IMPORTS DO SISTEMA RAG ====================
RAG_AVAILABLE = False
try:
    from rag_store import ingest_paths, search, context_from_hits
    from
