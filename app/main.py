# -*- coding: utf-8 -*-
import os
import time
import hmac
import hashlib
import logging
from typing import Optional, List

from fastapi import (
    FastAPI, Request, UploadFile, File, Header,
    Depends, Response, HTTPException, APIRouter
)
from fastapi.responses import (
    HTMLResponse, JSONResponse,
    RedirectResponse, PlainTextResponse
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

# RAG (sua base vetorial)
from .rag_store import ingest_paths, search, context_from_hits, load_pdf_text
from .core import answer

# -----------------------------------------------------------------------------
log = logging.getLogger("licitabot")
log.setLevel(logging.INFO)

app = FastAPI(title="Licitabot — Cloud")

# CORS para permitir que o painel admin/Swagger use multipart e leia JSON
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # pode fechar depois se quiser
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Localização de diretórios (templates, static, uploads e índice)
def _first_existing(candidates):
    for p in candidates:
        if os.path.isdir(p):
            return p
    # fallback pro primeiro, mesmo que não exista (pra debug)
    return candidates[0]

TEMPLATES_DIR = _first_existing([
    "templates",
    "app/templates",
    "/app/templates",
])

STATIC_DIR = _first_existing([
    "static",
    "app/static",
    "/app/static",
])

# pasta persistente dos PDFs:
# se você tem um Disk montado em /data no Render, usamos /data/uploaded_pdfs
# se não, cai no fallback dentro do container/app
DEFAULT_UPLOAD_BASE = "/data"
FALLBACK_UPLOAD_BASE = os.path.dirname(__file__)  # app/
if os.path.isdir(DEFAULT_UPLOAD_BASE):
    BASE_DIR = DEFAULT_UPLOAD_BASE
else:
    BASE_DIR = FALLBACK_UPLOAD_BASE

UPLOAD_DIR = os.path.join(BASE_DIR, "uploaded_pdfs")
CHROMA_DIR = os.path.join(BASE_DIR, "chroma")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)

# monta /static para servir CSS etc
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# -----------------------------------------------------------------------------
# Variáveis de ambiente que controlam acesso
ACCESS_PASSWORD    = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()
ADMIN_UPLOAD_TOKEN = (
    os.getenv("ADMIN_UPLOAD_TOKEN")
    or os.getenv("ADMIN_TOKEN")
    or "admin123"
).strip()
SECRET_KEY = (os.getenv("SECRET_KEY", "troque-este-segredo") or "troque-este-segredo").strip()

# -----------------------------------------------------------------------------
# Sessão simples com cookie
SESSION_COOKIE = "licita_sess"
SESSION_TTL    = 60 * 60 * 24 * 7  # 7 dias

def _make_token(username: str = "cliente") -> str:
    exp = int(time.time()) + SESSION_TTL
    payload = f"{username}:{exp}"
    sig = hmac.new(
        SECRET_KEY.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return f"{payload}:{sig}"

def _verify_token(token: str) -> bool:
    try:
        username, exp, sig = token.split(":", 2)
        payload = f"{username}:{exp}"
        expected = hmac.new(
            SECRET_KEY.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
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

# -----------------------------------------------------------------------------
# /health — status rápido
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
    }

# -----------------------------------------------------------------------------
# ROTAS DEBUG (só para você auditar — não mostrar a cliente final)
@app.get("/_debug/vars", response_class=JSONResponse)
def debug_vars(token: str):
    # proteção básica usando o token admin
    if token != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Não autorizado")
    try:
        files_now = sorted(os.listdir(UPLOAD_DIR))
    except Exception as e:
        files_now = [f"erro lendo UPLOAD_DIR: {e}"]

    return {
        "ACCESS_PASSWORD?set": bool(ACCESS_PASSWORD),
        "ADMIN_UPLOAD_TOKEN?set": bool(ADMIN_UPLOAD_TOKEN),
        "UPLOAD_DIR": UPLOAD_DIR,
        "FILES_IN_UPLOAD_DIR": files_now,
        "CHROMA_DIR": CHROMA_DIR,
    }

@app.get("/_debug/search", response_class=JSONResponse)
def debug_search(q: str, token: str):
    if token != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Não autorizado")

    hits = search(q, k=4)
    results = []
    for (doc, md) in hits:
        results.append({
            "source": md.get("source"),
            "chunk": md.get("chunk"),
            "excerpt": doc[:400],
        })

    return {
        "query": q,
        "results": results,
    }

# -----------------------------------------------------------------------------
# PÁGINA DO USUÁRIO (login + pergunta)
@app.get("/", response_class=HTMLResponse)
def page_login(request: Request):
    # Renderiza o login.html que tem:
    #  - campo senha
    #  - botão "Entrar"
    #  - campo pergunta + botão "Perguntar"
    # A lógica JS faz POST /login e POST /ask
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(payload: dict, response: Response):
    """
    Espera JSON tipo: {"password": "senha digitada"}
    Se bater com ACCESS_PASSWORD:
      - gera cookie de sessão
      - responde {"ok": true}
    """
    pwd = (payload or {}).get("password", "").strip()
    if pwd != ACCESS_PASSWORD:
        # senha errada
        return JSONResponse(
            {"ok": False, "error": "Senha incorreta."},
            status_code=401
        )

    token = _make_token("cliente")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax"
    )
    return resp

@app.post("/ask")
async def ask(
    payload: dict,
    ok: bool = Depends(_require_auth),
    x_admin_token: Optional[str] = Header(None),
):
    """
    Espera JSON: {"question": "..."}
    1. Faz busca vetorial (search)
    2. Gera resposta com answer()
    3. Se x_admin_token == ADMIN_UPLOAD_TOKEN -> inclui citações
    """
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

    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return {
            "answer": ans,
            "citations": [
                {
                    "source": md.get("source"),
                    "chunk": md.get("chunk"),
                    "excerpt": doc[:280]
                }
                for (doc, md) in hits
            ]
        }
    return {"answer": ans}

# -----------------------------------------------------------------------------
# ÁREA DO ADMINISTRADOR
router = APIRouter()

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """
    Renderiza admin.html:
    - campo senha admin (ADMIN_UPLOAD_TOKEN)
    - upload de PDF
    - botão listar PDFs
    - botão excluir
    """
    return templates.TemplateResponse("admin.html", {"request": request})


@router.get("/upload", include_in_schema=False)
def alias_upload():
    """
    "/upload" é só um atalho visual para você.
    Redireciona para /admin.
    """
    return RedirectResponse(url="/admin", status_code=307)


@router.post("/upload_pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None),
):
    """
    Fluxo:
    - Checa token admin
    - Garante que é .pdf
    - Salva em UPLOAD_DIR (que deve estar em /data/uploaded_pdfs no Render)
    - Roda ingest_paths() para indexar no Chroma
    - Faz pequeno preview de OCR/log
    """
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Envie apenas arquivos .pdf")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    destino = os.path.join(UPLOAD_DIR, file.filename)

    # salva o PDF fisicamente (stream seguro)
    try:
        with open(destino, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                buffer.write(chunk)
        log.info(f"[UPLOAD] PDF salvo em {destino}")
    except Exception as e:
        log.exception("Falha ao salvar PDF")
        raise HTTPException(
            status_code=500,
            detail=f"Falha ao salvar PDF: {type(e).__name__} - {e}"
        )

    # tentar ler texto bruto do PDF (inclui OCR quando disponível no rag_store)
    try:
        preview_txt = load_pdf_text(destino)[:500]
    except Exception as e:
        preview_txt = f"[ERRO AO LER TEXTO] {e}"
    log.info(f"[OCR PREVIEW] {preview_txt}")

    # indexar no banco vetorial
    try:
        ingest_paths([destino])
        indexed_ok = True
        idx_err = ""
        log.info(f"[INDEX] {file.filename} indexado no Chroma.")
    except Exception as e:
        indexed_ok = False
        idx_err = f"{type(e).__name__}: {e}"
        log.exception("Falha ao indexar PDF")

    return {
        "ok": True,
        "filename": file.filename,
        "saved_to": destino,
        "indexed": indexed_ok,
        "index_error": idx_err,
        "text_preview": preview_txt,
    }


@router.get("/list_pdfs")
async def list_pdfs(x_admin_token: Optional[str] = Header(None)):
    """
    Retorna lista dos PDFs armazenados em UPLOAD_DIR.
    Isso prova que estão persistidos em disco.
    """
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    itens = sorted([
        f for f in os.listdir(UPLOAD_DIR)
        if f.lower().endswith(".pdf")
    ])
    return {"files": itens}


@router.delete("/delete_pdf")
async def delete_pdf(
    name: str,
    x_admin_token: Optional[str] = Header(None)
):
    """
    Exclui um PDF e opcionalmente reindexa tudo
    (reindex simplificada: só chama ingest_paths pros restantes).
    """
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    alvo = os.path.join(UPLOAD_DIR, name)

    if not os.path.exists(alvo):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")

    try:
        os.remove(alvo)
        log.info(f"[DELETE] Removido {alvo}")

        # reindexa os PDFs que sobraram
        try:
            remanescentes = [
                os.path.join(UPLOAD_DIR, f)
                for f in os.listdir(UPLOAD_DIR)
                if f.lower().endswith(".pdf")
            ]
            if remanescentes:
                ingest_paths(remanescentes)
                log.info("[REINDEX] Base reindexada após exclusão.")
        except Exception as e:
            log.warning(f"[REINDEX] Falhou após exclusão: {e}")

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Falha ao excluir: {e}"
        )

    return {"ok": True, "deleted": name}


app.include_router(router)

# -----------------------------------------------------------------------------
# Checagem rápida de token admin via header
@app.get("/check_token", response_class=PlainTextResponse)
async def check_token(x_admin_token: Optional[str] = Header(None)):
    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return PlainTextResponse("✅ Token válido", status_code=200)
    return PlainTextResponse("❌ Token inválido", status_code=401)

# -----------------------------------------------------------------------------
# MAIN uvicorn (Render chama via Dockerfile/CMD)
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
