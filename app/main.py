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

# ---- RAG (suas dependências existentes) -------------------------------------
from .rag_store import ingest_paths, search, context_from_hits
from .core import answer

# -----------------------------------------------------------------------------
app = FastAPI(title="Licitabot — Cloud")
log = logging.getLogger("licitabot")
log.setLevel(logging.INFO)

# --------------------- Localização robusta dos diretórios --------------------
def _choose_dir(candidates):
    """Retorna o primeiro caminho existente na lista de candidatos."""
    for p in candidates:
        if os.path.isdir(p):
            return p
    return candidates[0]  # fallback (mesmo que não exista, para logar erro se houver)

# Se você usa Dockerfile copiando para /templates e /static, estes existirão:
TEMPLATES_DIR = _choose_dir(["templates", "app/templates", "aplicativo/templates"])
STATIC_DIR    = _choose_dir(["static", "app/static", "aplicativo/estatico", "aplicativo/static"])

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# --------------------------- Variáveis de ambiente ---------------------------
ACCESS_PASSWORD    = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()
ADMIN_UPLOAD_TOKEN = (os.getenv("ADMIN_UPLOAD_TOKEN") or os.getenv("ADMIN_TOKEN") or "admin123").strip()
SECRET_KEY         = (os.getenv("SECRET_KEY", "troque-este-segredo") or "troque-este-segredo").strip()

# Diretório PERSISTENTE para os PDFs:
DEFAULT_UPLOAD_DIR = "/data/uploaded_pdfs"
FALLBACK_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploaded_pdfs")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", DEFAULT_UPLOAD_DIR if os.path.isdir("/data") else FALLBACK_UPLOAD_DIR)
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
    }

# ------------------------ PÁGINA DO USUÁRIO ----------------------------------
@app.get("/", response_class=HTMLResponse)
def page_login(request: Request):
    """Página de login + pergunta (pergunta só libera após senha correta)."""
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
    """Consulta RAG: busca no índice e responde."""
    q = (payload or {}).get("question", "").strip()
    if not q:
        return {"answer": "Por favor, escreva sua pergunta."}

    try:
        hits = search(q, k=4)
        n_hits = len(hits) if hits else 0
        log.info(f"[SEARCH] Pergunta: '{q}' | {n_hits} resultados encontrados.")
    except Exception as e:
        log.exception("[SEARCH] Erro na busca")
        return {"answer": f"Erro na busca: {e}"}

    if not hits:
        return {"answer": "Não encontrei essa informação na base de documentos."}

    ctx = context_from_hits(hits)
    try:
        ans = answer(q, ctx)
    except Exception as e:
        log.exception("Falha ao consultar modelo")
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
    """Interface do administrador: upload, listagem e exclusão."""
    return templates.TemplateResponse("admin.html", {"request": request})

@router.get("/upload", include_in_schema=False)
def upload_alias():
    return RedirectResponse(url="/admin", status_code=307)

# ------------------------ UPLOAD COM CONFIRMAÇÃO DE INDEXAÇÃO ----------------
@router.post("/upload_pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None),
):
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
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
        log.info(f"[UPLOAD] Arquivo salvo em: {destino}")
    except Exception as e:
        log.exception("[UPLOAD] Falha ao salvar PDF")
        raise HTTPException(status_code=500, detail=f"Falha ao salvar PDF: {e}")

    try:
        ingest_paths([destino])
        log.info(f"[INGEST] Indexação concluída com sucesso: {file.filename}")
        return {
            "ok": True,
            "filename": file.filename,
            "indexed": True,
            "message": f"✅ Arquivo '{file.filename}' enviado e indexado com sucesso!"
        }
    except Exception as e:
        log.exception("[INGEST] Falha na indexação do PDF")
        return {
            "ok": True,
            "filename": file.filename,
            "indexed": False,
            "message": f"⚠️ Arquivo '{file.filename}' salvo, mas não foi indexado.",
            "index_error": str(e)
        }

@router.get("/list_pdfs")
async def list_pdfs(x_admin_token: Optional[str] = Header(None)):
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    itens = sorted([f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")])
    return {"files": itens}

@router.delete("/delete_pdf")
async def delete_pdf(name: str, x_admin_token: Optional[str] = Header(None)):
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    alvo = os.path.join(UPLOAD_DIR, name)
    if not os.path.exists(alvo):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    try:
        os.remove(alvo)
        log.info(f"[DELETE] Arquivo removido: {name}")
        paths = [os.path.join(UPLOAD_DIR, f) for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")]
        if paths:
            ingest_paths(paths)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao excluir: {e}")
    return {"ok": True, "deleted": name}

app.include_router(router)

# --------------------- DIAGNÓSTICO SIMPLES DO TOKEN --------------------------
@app.get("/check_token", response_class=PlainTextResponse)
async def check_token(x_admin_token: Optional[str] = Header(None)):
    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return PlainTextResponse("✅ Token válido", status_code=200)
    return PlainTextResponse("❌ Token inválido", status_code=401)

# --------------------- STATUS DE DEPURAÇÃO DO SISTEMA ------------------------
@app.get("/debug_status")
def debug_status():
    """Verifica se há PDFs e se o índice do RAG existe."""
    arquivos = [f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")]
    index_dir = os.path.join(os.path.dirname(__file__), "data")  # ajuste se o rag_store usar outro
    index_ok = os.path.isdir(index_dir) and any(os.scandir(index_dir))
    return {
        "upload_dir": UPLOAD_DIR,
        "pdfs_encontrados": arquivos,
        "indice_existente": index_ok,
        "indice_diretorio": index_dir,
        "total_pdfs": len(arquivos)
    }

# ----------------------------- UVICORN (Render) ------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
