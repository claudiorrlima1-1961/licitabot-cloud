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

# ================== Dependências do RAG (as suas) ==================
from .rag_store import ingest_paths, search, context_from_hits
from .core import answer
# ===================================================================

app = FastAPI(title="Licitabot – Cloud")

# Assets/templating
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# -------------------- Variáveis de ambiente ------------------------
ACCESS_PASSWORD    = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()
ADMIN_UPLOAD_TOKEN = (os.getenv("ADMIN_UPLOAD_TOKEN") or os.getenv("ADMIN_TOKEN") or "admin123").strip()
SECRET_KEY         = (os.getenv("SECRET_KEY", "troque-este-segredo") or "troque-este-segredo").strip()

# Diretório PERSISTENTE (Render Disk em /data). Pode ajustar via env UPLOAD_DIR.
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploaded_pdfs")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ----------------------- Sessão simples (cookie) -------------------
SESSION_COOKIE = "licita_sess"
SESSION_TTL    = 60 * 60 * 24 * 7  # 7 dias

def make_token(username: str = "cliente") -> str:
    exp = int(time.time()) + SESSION_TTL
    payload = f"{username}:{exp}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"

def verify_token(token: str) -> bool:
    try:
        username, exp, sig = token.split(":", 2)
        payload = f"{username}:{exp}"
        expected = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return False
        return int(exp) >= int(time.time())
    except Exception:
        return False

def require_auth(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not verify_token(token):
        raise HTTPException(status_code=401, detail="Acesso não autorizado.")
    return True

# ========================= Páginas do CLIENTE ======================
@app.get("/", response_class=HTMLResponse)
def page_login(request: Request):
    """Tela de login do cliente."""
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(payload: dict, response: Response):
    """Valida senha do cliente e grava cookie de sessão."""
    pwd = (payload or {}).get("password", "").strip()
    if pwd != ACCESS_PASSWORD:
        return JSONResponse({"ok": False, "error": "Senha incorreta."}, status_code=401)
    token = make_token("cliente")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax")
    return resp

@app.get("/chat", response_class=HTMLResponse)
def page_chat(request: Request):
    """Página de perguntas do cliente."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not verify_token(token):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request})

# ============================== Chat (/ask) ========================
@app.post("/ask")
async def ask(
    payload: dict,
    ok: bool = Depends(require_auth),
    x_admin_token: Optional[str] = Header(None)
):
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

    # Citações só se vier cabeçalho de admin
    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return {
            "answer": ans,
            "citations": [
                {"source": md.get("source"), "chunk": md.get("chunk"), "excerpt": doc[:280]}
                for (doc, md) in hits
            ]
        }
    return {"answer": ans}

# ===================== Painel do ADMIN (página) ===================
@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    """
    Painel do administrador (upload/listagem/indexação).
    O HTML deve estar em templates/upload.html (seu layout atual).
    """
    return templates.TemplateResponse("upload.html", {"request": request})

# ==================== Upload/Index/Diagnóstico ====================
@app.post("/upload_pdf", response_class=JSONResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None),
):
    """
    Upload leve: apenas salva o PDF no disco persistente.
    A indexação é feita separadamente em /reindex (chamada pela própria página admin).
    """
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    nome = (file.filename or "").strip()
    if not nome.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Envie apenas arquivos .pdf")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    destino = os.path.join(UPLOAD_DIR, nome)

    try:
        with open(destino, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MB
                if not chunk:
                    break
                buffer.write(chunk)

        # Confirma gravação
        with open(destino, "rb") as fh:
            _ = fh.read(1024)

        return {"ok": True, "filename": nome, "saved_to": destino}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao salvar PDF: {type(e).__name__} - {e}")

@app.post("/reindex", response_class=JSONResponse)
def reindex_all(x_admin_token: Optional[str] = Header(None)):
    """
    Reindexa TODOS os PDFs presentes em UPLOAD_DIR.
    Chame após realizar uploads. Evita 502 por tempo/memória no upload.
    """
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    if not os.path.isdir(UPLOAD_DIR):
        return {"ok": True, "indexed": 0, "message": "Nenhum diretório de uploads."}

    pdfs: List[str] = [
        os.path.join(UPLOAD_DIR, f)
        for f in os.listdir(UPLOAD_DIR)
        if f.lower().endswith(".pdf")
    ]
    if not pdfs:
        return {"ok": True, "indexed": 0, "message": "Nenhum PDF encontrado."}

    try:
        ingest_paths(pdfs)
        return {"ok": True, "indexed": len(pdfs), "files": [os.path.basename(p) for p in pdfs]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

@app.get("/admin_diag", response_class=JSONResponse)
def admin_diag(x_admin_token: Optional[str] = Header(None)):
    """
    Lista os PDFs no servidor (usado pelo botão 'Ver PDFs' da página admin).
    """
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    exists = os.path.isdir(UPLOAD_DIR)
    files = []
    if exists:
        files = sorted([f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")])

    return {
        "UPLOAD_DIR": UPLOAD_DIR,
        "UPLOAD_DIR_exists": exists,
        "UPLOAD_files": files,
    }

# ======================= Checagem de token (admin) ============================
@app.get("/check_token", response_class=PlainTextResponse)
async def check_token(x_admin_token: Optional[str] = Header(None)):
    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return PlainTextResponse("✅ Token válido", status_code=200)
    else:
        return PlainTextResponse("❌ Token inválido", status_code=401)

# ======================== Execução local (uvicorn) ============================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
