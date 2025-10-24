# -*- coding: utf-8 -*-
import os
import time
import hmac
import hashlib
import logging
from typing import Optional, List

from fastapi import (
    FastAPI, Request, UploadFile, File, Header,
    Depends, Response, HTTPException
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---- Suas dependências de RAG (mantidas) ------------------------------------
from .rag_store import ingest_paths, search, context_from_hits
from .core import answer

# -----------------------------------------------------------------------------
app = FastAPI(title="Licitabot – Cloud")

# Pastas de assets
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# -------------------- Variáveis de ambiente (com .strip) ---------------------
ACCESS_PASSWORD    = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()
ADMIN_UPLOAD_TOKEN = (os.getenv("ADMIN_UPLOAD_TOKEN") or os.getenv("ADMIN_TOKEN") or "admin123").strip()
SECRET_KEY         = (os.getenv("SECRET_KEY", "troque-este-segredo") or "troque-este-segredo").strip()

# Diretório de uploads (persistente no Render se você apontar para /data)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "app/uploaded_pdfs")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ----------------------------- Sessão simples --------------------------------
SESSION_COOKIE = "licita_sess"
SESSION_TTL    = 60 * 60 * 24 * 7  # 7 dias

def _make_token(username: str = "cliente") -> str:
    exp = int(time.time()) + SESSION_TTL
    payload = f"{username}:{exp}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"

def _verify_token(token: str) -> bool:
    try:
        username, exp, sig = token.split(":", 2)
        payload = f"{username}:{exp}"
        expected = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return False
        return int(exp) >= int(time.time())
    except Exception:
        return False

def _require_auth(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not _verify_token(token):
        raise HTTPException(status_code=401, detail="Acesso não autorizado.")
    return True

# ------------------------------- Páginas (Cliente) ----------------------------
@app.get("/", response_class=HTMLResponse)
def page_login(request: Request):
    """Tela de login do cliente."""
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(payload: dict, response: Response):
    """Valida senha de acesso do cliente e seta cookie de sessão."""
    pwd = (payload or {}).get("password", "").strip()
    if pwd != ACCESS_PASSWORD:
        return JSONResponse({"ok": False, "error": "Senha incorreta."}, status_code=401)
    token = _make_token("cliente")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax")
    return resp

@app.get("/chat", response_class=HTMLResponse)
def page_chat(request: Request):
    """Página do chat (cliente)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not _verify_token(token):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request})

# ------------------------------- Chat (/ask) ----------------------------------
@app.post("/ask")
async def ask(payload: dict, ok: bool = Depends(_require_auth), x_admin_token: Optional[str] = Header(None)):
    """Pergunta do cliente usando RAG."""
    q = (payload or {}).get("question", "").strip()
    if not q:
        return {"answer": "Por favor, escreva sua pergunta."}

    hits = search(q, k=4)
    if not hits:
        return {"answer": "Não encontrei essa informação na base de documentos."}

    ctx = context_from_hits(hits)
    try:
        ans = answer(q, ctx)
    except Exception as e:
        ans = f"Erro ao consultar o modelo: {e}"

    # Se mandar X-Admin-Token válido, retorna também citações
    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return {
            "answer": ans,
            "citations": [
                {"source": md.get("source"), "chunk": md.get("chunk"), "excerpt": doc[:280]}
                for (doc, md) in hits
            ]
        }
    return {"answer": ans}

# ----------------------------- Painel do Administrador ------------------------
@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    """
    Painel de Upload/Lista/Reindex (HTML pronto em templates/upload.html).
    Esta rota é separada do cliente.
    """
    with open("templates/upload.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# ------------------------------- Upload de PDFs -------------------------------
@app.post("/upload_pdf", response_class=JSONResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None)
):
    # Segurança do admin (tolera espaços acidentais)
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    # Somente PDF
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Envie apenas arquivos .pdf")

    # Salvar em chunks para evitar 502 por requisição longa
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        destino = os.path.join(UPLOAD_DIR, file.filename)

        with open(destino, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MB
                if not chunk:
                    break
                buffer.write(chunk)

        # Confere leitura
        with open(destino, "rb") as fh:
            _ = fh.read(1024)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao salvar PDF: {type(e).__name__} - {e}")

    return {"status": "ok", "filename": file.filename, "saved_to": destino}

# ------------------------------- Reindexação ----------------------------------
@app.post("/reindex", response_class=JSONResponse)
def reindex(x_admin_token: Optional[str] = Header(None)):
    """
    Revarre o diretório UPLOAD_DIR e indexa todos os PDFs.
    Chamar após o upload (a página /admin já chama sozinha).
    """
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    if not os.path.exists(UPLOAD_DIR):
        return {"ok": False, "error": f"Pasta não encontrada: {UPLOAD_DIR}"}

    # lista PDFs
    files: List[str] = [
        os.path.join(UPLOAD_DIR, f)
        for f in os.listdir(UPLOAD_DIR)
        if f.lower().endswith(".pdf")
    ]
    if not files:
        return {"ok": True, "indexed": 0, "files": []}

    try:
        ingest_paths(files)
        return {"ok": True, "indexed": len(files), "files": [os.path.basename(p) for p in files]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha na indexação: {type(e).__name__} - {e}")

# ------------------------------ Diagnóstico admin -----------------------------
@app.get("/admin_diag", response_class=JSONResponse)
def admin_diag(x_admin_token: Optional[str] = Header(None)):
    """Lista o conteúdo do diretório de uploads (para o botão 'Ver PDFs')."""
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    data = {
        "UPLOAD_DIR": UPLOAD_DIR,
        "UPLOAD_DIR_exists": os.path.exists(UPLOAD_DIR),
        "UPLOAD_files": []
    }
    if data["UPLOAD_DIR_exists"]:
        data["UPLOAD_files"] = sorted([f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")])
    return data

# ------------------------------ Teste de token --------------------------------
@app.get("/check_token", response_class=PlainTextResponse)
async def check_token(x_admin_token: Optional[str] = Header(None)):
    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return PlainTextResponse("✅ Token válido", status_code=200)
    else:
        return PlainTextResponse("❌ Token inválido", status_code=401)

# ------------------------------ Uvicorn (Render) -----------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
