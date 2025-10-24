# -*- coding: utf-8 -*-
# app/main.py
import os
import time
import hmac
import hashlib
import logging
from typing import Optional, List

from fastapi import (
    FastAPI, Request, UploadFile, File, Header, Depends,
    Response, HTTPException, APIRouter
)
from fastapi.responses import (
    HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# === RAG (mantido) ============================================================
from .rag_store import ingest_paths, search, context_from_hits
from .core import answer

# === App base =================================================================
app = FastAPI(title="Licitabot – Cloud")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# === Variáveis de ambiente (sempre .strip para evitar \n/ espaços) ============
ACCESS_PASSWORD    = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()
ADMIN_UPLOAD_TOKEN = (
    os.getenv("ADMIN_UPLOAD_TOKEN")
    or os.getenv("ADMIN_TOKEN")
    or "admin123"
).strip()
SECRET_KEY         = (os.getenv("SECRET_KEY", "troque-este-segredo") or "troque-este-segredo").strip()

# === Sessão simples ===========================================================
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
        return hmac.compare_digest(expected, sig) and int(exp) >= int(time.time())
    except Exception:
        return False

def require_auth(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not _verify_token(token):
        raise HTTPException(status_code=401, detail="Acesso não autorizado.")
    return True

# === Rotas (Cliente) ==========================================================
@app.get("/", response_class=HTMLResponse)
def page_login(request: Request):
    """Página inicial: LOGIN do cliente (NÃO admin)."""
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

@app.get("/chat", response_class=HTMLResponse)
def page_chat(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not _verify_token(token):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/ask")
async def ask(payload: dict, ok: bool = Depends(require_auth), x_admin_token: Optional[str] = Header(None)):
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

    # Se vier o cabeçalho de admin correto, devolve as citações também
    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return {
            "answer": ans,
            "citations": [
                {"source": md.get("source"), "chunk": md.get("chunk"), "excerpt": doc[:280]}
                for (doc, md) in hits
            ]
        }
    return {"answer": ans}

# === Rotas (Admin) ============================================================
router = APIRouter()
log = logging.getLogger("upload")
log.setLevel(logging.INFO)

# Pasta de destino: fica dentro do repositório (garantir .gitkeep)
BASE_DIR   = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploaded_pdfs")

@router.get("/admin", response_class=HTMLResponse)
async def upload_page(request: Request):
    """Tela do ADMIN (upload). Mantém seu layout bonitão em templates/upload.html."""
    return templates.TemplateResponse("upload.html", {"request": request})

@router.post("/upload_pdf", response_class=JSONResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None),  # aceita "X-Admin-Token"
):
    # 1) Autorização do admin (normaliza espaços)
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    # 2) Somente PDF
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Envie apenas arquivos .pdf")

    # 3) Gravação robusta (chunks) e verificação
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        destino = os.path.join(UPLOAD_DIR, file.filename)

        with open(destino, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                buffer.write(chunk)

        if not os.path.exists(destino) or os.path.getsize(destino) == 0:
            raise RuntimeError("Falha na gravação do arquivo.")

        log.info(f"[UPLOAD] PDF salvo: {destino}")

        # 4) Indexação (RAG)
        try:
            ingest_paths([destino])
        except Exception as ie:
            log.exception("Falha ao indexar PDF (continua OK para o upload).")
            return {"status": "ok", "filename": file.filename, "saved_to": f"app/uploaded_pdfs/{file.filename}", "index_error": str(ie)}

    except HTTPException:
        raise
    except Exception as e:
        log.exception("Falha ao salvar PDF")
        raise HTTPException(status_code=500, detail=f"Falha ao salvar PDF: {type(e).__name__} - {e}")

    return {"status": "ok", "filename": file.filename, "saved_to": f"app/uploaded_pdfs/{file.filename}"}

@router.get("/list_pdfs", response_class=JSONResponse)
async def list_pdfs(x_admin_token: Optional[str] = Header(None)):
    """Lista PDFs já enviados (para o botão 'Ver PDFs')."""
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    arquivos = [f for f in sorted(os.listdir(UPLOAD_DIR)) if f.lower().endswith(".pdf")]
    return {"files": arquivos}

# Diagnósticos de token (úteis para suporte)
@app.get("/testar_token", response_class=HTMLResponse)
async def testar_token_page():
    html = """
    <!doctype html><html lang="pt-BR"><head>
      <meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
      <title>Teste de Token — Licitabot</title>
      <style>
        body{font-family:Arial,Helvetica,sans-serif;background:#f4f6f8;color:#1f2937;
             display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}
        .card{background:#fff;padding:24px;border-radius:16px;box-shadow:0 8px 20px rgba(0,0,0,.1);
              max-width:360px;width:100%;text-align:center;}
        input{width:100%;padding:10px;margin:12px 0;border:1px solid #ccc;border-radius:8px;}
        button{width:100%;background:#0b3d5c;color:#fff;border:none;padding:10px;border-radius:8px;cursor:pointer;}
        .ok{color:#166534}.err{color:#991b1b} #msg{margin-top:12px;font-weight:600;}
      </style></head><body>
      <div class="card">
        <h2>🔑 Teste de Token (Admin)</h2>
        <p>Digite a senha configurada no Render (ADMIN_UPLOAD_TOKEN).</p>
        <input type="password" id="token" placeholder="Senha do admin"/>
        <button onclick="verificar()">Verificar</button>
        <div id="msg"></div>
      </div>
      <script>
        async function verificar(){
          const senha = document.getElementById('token').value.trim();
          const r = await fetch('/check_token', {headers:{'X-Admin-Token':senha}});
          const t = await r.text(); document.getElementById('msg').innerHTML =
            r.ok ? '<span class="ok">'+t+'</span>' : '<span class="err">'+t+'</span>';
        }
      </script></body></html>
    """
    return HTMLResponse(html)

@app.get("/check_token", response_class=PlainTextResponse)
async def check_token(x_admin_token: Optional[str] = Header(None)):
    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return PlainTextResponse("✅ Token válido", status_code=200)
    return PlainTextResponse("❌ Token inválido", status_code=401)

# Encaixa o router admin
app.include_router(router)

# === Execução local (Render usa o start command configurado) ==================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
