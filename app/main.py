# -*- coding: utf-8 -*-
import os
import time
import hmac
import hashlib
import logging
from typing import Optional, List

from fastapi import (
    FastAPI, Request, UploadFile, File, Header, Depends, Response, HTTPException, APIRouter
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------- BASE DE CAMINHOS (funciona no Render e local) ----------
from pathlib import Path
BASE_DIR = Path(__file__).parent                 # .../aplicativo
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploaded_pdfs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ---------- APP ----------
app = FastAPI(title="Licitabot — Cloud")
log = logging.getLogger("licitabot")
log.setLevel(logging.INFO)

# Estáticos / Templates
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# PDFs servidos em /pdfs/NOME.pdf (opcional abrir em nova aba)
app.mount("/pdfs", StaticFiles(directory=str(UPLOAD_DIR)), name="pdfs")

# ---------- VARIÁVEIS DE AMBIENTE ----------
ACCESS_PASSWORD     = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()
ADMIN_UPLOAD_TOKEN  = (os.getenv("ADMIN_UPLOAD_TOKEN") or os.getenv("ADMIN_TOKEN") or "admin123").strip()
SECRET_KEY          = (os.getenv("SECRET_KEY", "troque-este-segredo") or "troque-este-segredo").strip()

# ---------- SESSÃO (libera perguntas após senha) ----------
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

# ---------- SUAS DEPENDÊNCIAS DE RAG ----------
# Se o seu arquivo chama nucleo.py, RENOMEIE para core.py.
from .rag_store import ingest_paths, search, context_from_hits
from .core import answer

# ------------------------ SAÚDE ----------------------------------------------
@app.get("/health")
def health():
    try:
        ok = bool(search("teste", k=1) is not None)
    except Exception:
        ok = False
    return {"status": "online", "rag": ok}

# ------------------------ PÁGINA DO USUÁRIO ----------------------------------
@app.get("/", response_class=HTMLResponse)
def page_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(payload: dict, response: Response):
    pwd = (payload or {}).get("password", "").strip()
    if pwd != ACCESS_PASSWORD:
        return JSONResponse({"ok": False, "error": "Senha incorreta."}, status_code=401)
    token = _make_token("cliente")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax")
    return resp

@app.post("/ask")
async def ask(payload: dict, ok: bool = Depends(_require_auth), x_admin_token: Optional[str] = Header(None)):
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

    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return {
            "answer": ans,
            "citations": [
                {"source": md.get("source"), "chunk": md.get("chunk"), "excerpt": doc[:280]}
                for (doc, md) in hits
            ]
        }
    return {"answer": ans}

# ------------------------ PÁGINA DO ADMINISTRADOR ----------------------------
router = APIRouter()

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    # Renderiza admin.html. Se faltar, mostra diagnóstico claro.
    try:
        esperado = TEMPLATES_DIR / "admin.html"
        if not esperado.exists():
            return HTMLResponse(
                f"<h3>Template não encontrado:</h3><pre>{esperado}</pre>"
                f"<p>Crie <code>aplicativo/templates/admin.html</code> no repositório.</p>", status_code=500
            )
        return templates.TemplateResponse("admin.html", {"request": request})
    except Exception as e:
        return HTMLResponse(
            f"<h3>Erro ao renderizar admin.html</h3><pre>{type(e).__name__}: {e}</pre>", status_code=500
        )

# atalho opcional
@router.get("/upload", response_class=HTMLResponse)
async def upload_alias(request: Request):
    return await admin_page(request)

@router.post("/upload_pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None),
):
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Envie apenas arquivos .pdf")

    destino = UPLOAD_DIR / file.filename
    try:
        with open(destino, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar PDF: {e}")

    # Indexa no RAG
    indexed, index_error = True, None
    try:
        ingest_paths([str(destino)])
    except Exception as e:
        indexed, index_error = False, str(e)
        log.exception("Falha ao indexar PDF")

    return {"ok": True, "filename": file.filename, "indexed": indexed, "index_error": index_error}

@router.get("/list_pdfs")
async def list_pdfs(x_admin_token: Optional[str] = Header(None)):
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    files = sorted([f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")])
    return {"files": files}

@router.delete("/delete_pdf")
async def delete_pdf(name: str, x_admin_token: Optional[str] = Header(None)):
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    alvo = UPLOAD_DIR / name
    if not alvo.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    os.remove(alvo)
    # (Opcional) reindexar o restante
    try:
        restantes = [str(UPLOAD_DIR / f) for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")]
        if restantes:
            ingest_paths(restantes)
    except Exception:
        pass
    return {"ok": True, "deleted": name}

app.include_router(router)

# --------------------- DIAGNÓSTICO DO TOKEN ----------------------------------
@app.get("/check_token", response_class=PlainTextResponse)
async def check_token(x_admin_token: Optional[str] = Header(None)):
    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return PlainTextResponse("✅ Token válido", status_code=200)
    return PlainTextResponse("❌ Token inválido", status_code=401)

# ----------------------------- UVICORN (Render) ------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("aplicativo.main:app", host="0.0.0.0", port=port)
