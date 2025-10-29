# -*- coding: utf-8 -*-
import os
import time
import hmac
import hashlib
import logging
from typing import Optional, List

from fastapi import (
    FastAPI, Request, UploadFile, File, Header, Depends, Response,
    HTTPException, APIRouter, Query
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---- RAG ---------------------------------------------------------------
from .rag_store import ingest_paths, search, context_from_hits
from .core import answer

# -----------------------------------------------------------------------
app = FastAPI(title="Licitabot — Cloud")
log = logging.getLogger("licitabot")
log.setLevel(logging.INFO)

# ---------------- Localização robusta dos diretórios -------------------
def _choose_dir(candidates):
    for p in candidates:
        if os.path.isdir(p):
            return p
    return candidates[0]

TEMPLATES_DIR = _choose_dir(["templates", "app/templates", "aplicativo/templates"])
STATIC_DIR    = _choose_dir(["static", "app/static", "aplicativo/static", "aplicativo/estatico"])

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# -------------------------- Variáveis ---------------------------------
ACCESS_PASSWORD    = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()
ADMIN_UPLOAD_TOKEN = (os.getenv("ADMIN_UPLOAD_TOKEN") or os.getenv("ADMIN_TOKEN") or "admin123").strip()
SECRET_KEY         = (os.getenv("SECRET_KEY", "troque-este-segredo") or "troque-este-segredo").strip()

# Diretórios persistentes
DEFAULT_UPLOAD_DIR   = "/data/uploaded_pdfs"
FALLBACK_UPLOAD_DIR  = os.path.join(os.path.dirname(__file__), "uploaded_pdfs")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", DEFAULT_UPLOAD_DIR if os.path.isdir("/data") else FALLBACK_UPLOAD_DIR)
os.makedirs(UPLOAD_DIR, exist_ok=True)

CHROMA_DIR = os.getenv("CHROMA_DIR", "/data/chroma")

# --------------------------- Sessão simples ----------------------------
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

def _is_admin(x_admin_token: Optional[str], token_query: Optional[str]) -> bool:
    token = (x_admin_token or token_query or "").strip()
    return token == ADMIN_UPLOAD_TOKEN

# --------------------------------- Saúde -------------------------------------
@app.get("/health")
def health():
    try:
        ok = bool(search("teste", k=1) is not None)
    except Exception:
        ok = False
    return {
        "status": "online",
        "rag": ok,
        "templates_dir": TEMPLATES_DIR,
        "static_dir": STATIC_DIR,
        "upload_dir": UPLOAD_DIR,
        "chroma_dir": CHROMA_DIR,
        "files_in_upload": [f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")] if os.path.isdir(UPLOAD_DIR) else [],
    }

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
async def ask(
    payload: dict,
    ok: bool = Depends(_require_auth),
    x_admin_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
    debug: Optional[int] = Query(0),
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
        log.exception("Falha ao consultar modelo")
        ans = f"Erro ao consultar o modelo: {e}"

    if debug or _is_admin(x_admin_token, token):
        return {
            "answer": ans,
            "citations": [
                {"source": md.get("source"), "chunk": md.get("chunk"), "excerpt": doc[:320]}
                for (doc, md) in hits
            ],
            "used_context": ctx[:1200]
        }
    return {"answer": ans}

# ------------------------ PÁGINA DO ADMINISTRADOR ----------------------------
router = APIRouter()

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

@router.get("/upload", include_in_schema=False)
def upload_alias():
    return RedirectResponse(url="/admin", status_code=307)

@router.post("/upload_pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
):
    if not _is_admin(x_admin_token, token):
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Envie apenas arquivos .pdf")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    destino = os.path.join(UPLOAD_DIR, file.filename)

    try:
        with open(destino, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                buffer.write(chunk)
    except Exception as e:
        log.exception("Falha ao salvar PDF")
        raise HTTPException(status_code=500, detail=f"Falha ao salvar PDF: {type(e).__name__} - {e}")

    # Indexação do arquivo recém-enviado
    try:
        ingest_paths([destino])
        indexed = True
        index_error = None
    except Exception as e:
        log.exception("Falha ao indexar PDF")
        indexed = False
        index_error = f"{type(e).__name__}: {e}"

    return {"ok": True, "filename": file.filename, "indexed": indexed, "index_error": index_error}

@router.get("/list_pdfs")
async def list_pdfs(
    x_admin_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
):
    if not _is_admin(x_admin_token, token):
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    itens = sorted([f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")])
    return {"files": itens}

@router.delete("/delete_pdf")
async def delete_pdf(
    name: str,
    x_admin_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
):
    if not _is_admin(x_admin_token, token):
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    alvo = os.path.join(UPLOAD_DIR, name)
    if not os.path.exists(alvo):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    try:
        os.remove(alvo)
        try:
            # reindexa o que sobrou
            paths = [os.path.join(UPLOAD_DIR, f) for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")]
            if paths:
                ingest_paths(paths)
        except Exception:
            pass
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao excluir: {e}")
    return {"ok": True, "deleted": name}

# --------- NOVOS ENDPOINTS DE DIAGNÓSTICO ------------------------------------

@router.post("/admin/reindex")
async def admin_reindex(
    x_admin_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
):
    if not _is_admin(x_admin_token, token):
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    if not os.path.isdir(UPLOAD_DIR):
        raise HTTPException(status_code=500, detail="UPLOAD_DIR inexistente.")
    paths = [os.path.join(UPLOAD_DIR, f) for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")]
    if not paths:
        return {"ok": True, "ingested": 0, "detail": "Sem PDFs no diretório."}
    n = ingest_paths(paths)
    return {"ok": True, "ingested": n, "files": [os.path.basename(p) for p in paths]}

@router.get("/_debug/vars")
async def debug_vars(
    x_admin_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
):
    if not _is_admin(x_admin_token, token):
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    return {
        "templates_dir": TEMPLATES_DIR,
        "static_dir": STATIC_DIR,
        "upload_dir": UPLOAD_DIR,
        "chroma_dir": CHROMA_DIR,
        "upload_exists": os.path.isdir(UPLOAD_DIR),
        "files_in_upload": sorted([f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")]) if os.path.isdir(UPLOAD_DIR) else [],
    }

@router.get("/_debug/search")
async def debug_search(
    q: str = Query(..., description="Consulta"),
    x_admin_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
    k: int = Query(4),
):
    if not _is_admin(x_admin_token, token):
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    hits = search(q, k=k)
    return {
        "query": q,
        "k": k,
        "results": [
            {"source": md.get("source"), "chunk": md.get("chunk"), "excerpt": doc[:380]}
            for (doc, md) in hits
        ],
        "count": len(hits),
    }

app.include_router(router)

# ----------------------------- UVICORN ---------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
