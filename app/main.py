from fastapi import (
FastAPI, Request, UploadFile, File, Header,
HTTPException,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os, pathlib, hmac, hashlib, time

from .rag_store import ingest_paths, search, context_from_hits
from .core import answer

app = FastAPI(title="Licitabot – Cloud")

# Monta pastas
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Variáveis (strip para remover \n/ espaços)
ACCESS_PASSWORD = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()
ADMIN_TOKEN     = (os.getenv("ADMIN_TOKEN", "admin123") or "admin123").strip()
SECRET_KEY      = (os.getenv("SECRET_KEY", "troque-este-segredo") or "troque-este-segredo").strip()

# Sessão
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

# ---- Rotas ----

@app.get("/", response_class=HTMLResponse)
def page_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(payload: dict, response: Response):
    pwd = (payload or {}).get("password", "").strip()
    if pwd != ACCESS_PASSWORD:
        return JSONResponse({"ok": False, "error": "Senha incorreta."}, status_code=401)
    token = make_token("cliente")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_TTL, httponly=True, samesite="lax"
    )
    return resp

@app.get("/chat", response_class=HTMLResponse)
def page_chat(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not verify_token(token):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/ask")
async def ask(payload: dict, ok: bool = Depends(require_auth), x_admin_token: str = Header(None)):
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

    if x_admin_token == ADMIN_TOKEN:
        return {
            "answer": ans,
            "citations": [
                {"source": md.get("source"), "chunk": md.get("chunk"), "excerpt": doc[:280]}
                for (doc, md) in hits
            ]
        }
    return {"answer": ans}

@app.post("/upload_pdf")
async def upload_pdf(file: UploadFile = File(...), x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token de admin inválido.")
    pathlib.Path("/data/docs").mkdir(parents=True, exist_ok=True)
    dest = f"/data/docs/{file.filename}"
    with open(dest, "wb") as f:
        f.write(await file.read())
    ingest_paths([dest])
    return {"ok": True, "indexed": file.filename}
    from fastapi import UploadFile, File, Header, HTTPException
from typing import Optional
import os

# # # ====================== BLOCO DE UPLOAD (ADMIN) ======================
import os
import logging
from typing import Optional

# IMPORTAÇÕES CERTAS (inclui HTTPException!)
from fastapi import APIRouter, UploadFile, File, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

log = logging.getLogger("upload")
log.setLevel(logging.INFO)

router = APIRouter()

# Senha do admin (configure no Render → Environment → ADMIN_UPLOAD_TOKEN)
ADMIN_UPLOAD_TOKEN = os.getenv("ADMIN_UPLOAD_TOKEN", "admin123")

# Pasta de destino (no Render, /tmp é sempre gravável)
UPLOAD_DIR = "/tmp"

def _verifica_gravacao(caminho: str) -> None:
    """Confere se o arquivo foi gravado e é legível."""
    if not os.path.exists(caminho):
        raise RuntimeError("Arquivo não foi encontrado após o upload.")
    with open(caminho, "rb") as fh:
        _ = fh.read(1024)  # valida leitura

@router.post("/upload_pdf", response_class=JSONResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None, convert_underscores=False),
):
    # 1) Segurança (token de admin)
    if not x_admin_token or x_admin_token != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")

    # 2) Tipo do arquivo
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Envie apenas arquivos .pdf")

    # 3) Gravar em /tmp com escrita em blocos
    try:
        destino = os.path.join(UPLOAD_DIR, file.filename)

        # grava em chunks de 1MB (não estoura memória)
        with open(destino, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                buffer.write(chunk)

        _verifica_gravacao(destino)
        log.info(f"[UPLOAD] PDF salvo: {destino}")

    except Exception as e:
        log.exception("Falha ao salvar PDF")
        raise HTTPException(status_code=500, detail=f"Falha ao salvar PDF: {type(e).__name__} - {e}")

    # 4) (Opcional) Aqui você pluga a indexação do seu RAG no futuro
    return {"status": "ok", "filename": file.filename, "saved_to": destino}

@router.get("/upload", response_class=HTMLResponse)
async def upload_page():
    """Tela simples para enviar 1+ PDFs (a validação real é no /upload_pdf)."""
    html = """
    <!doctype html>
    <html lang="pt-BR">
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <title>Upload de PDFs — Licitabot</title>
      <style>
        :root{--txt:#1f2937;--muted:#475569;--btn:#0b3d5c;}
        html,body{height:100%;margin:0}
        body{font-family:Arial,Helvetica,sans-serif;background:#f5f7fa;color:var(--txt);
             display:flex;align-items:center;justify-content:center;padding:24px;}
        .card{width:100%;max-width:460px;background:#fff;border-radius:16px;
              box-shadow:0 10px 28px rgba(0,0,0,.12);padding:24px;}
        h1{margin:0 0 8px;font-size:22px;color:#0b2942}
        .sub{margin:0 0 16px;font-size:14px;color:var(--muted)}
        input,button{width:100%;box-sizing:border-box}
        input[type=password],input[type=file]{margin:8px 0 12px;padding:10px;border:1px solid #cbd5e1;border-radius:10px}
        button{background:var(--btn);color:#fff;border:none;padding:12px;border-radius:10px;font-weight:600;cursor:pointer}
        button:hover{filter:brightness(1.05)}
        #status{margin-top:12px;font-size:14px;max-height:240px;overflow:auto}
        .ok{color:#166534}.err{color:#991b1b}
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Envio de PDFs (Admin)</h1>
        <p class="sub">Digite a senha do administrador e selecione um ou mais PDFs.</p>
        <input id="senha" type="password" placeholder="Senha do admin (ADMIN_UPLOAD_TOKEN)"/>
        <input id="arquivo" type="file" accept="application/pdf" multiple/>
        <button onclick="enviar()">Enviar</button>
        <div id="status"></div>
      </div>

      <script>
        async function enviar(){
          const senha = document.getElementById('senha').value;
          const arquivos = document.getElementById('arquivo').files;
          const status = document.getElementById('status');
          status.innerHTML = '';
          if(!senha){ status.innerHTML='<p class="err">Informe a senha.</p>'; return; }
          if(!arquivos.length){ status.innerHTML='<p class="err">Selecione ao menos 1 PDF.</p>'; return; }

          for(let i=0;i<arquivos.length;i++){
            const fd = new FormData(); fd.append('file', arquivos[i]);
            try{
              const r = await fetch('/upload_pdf', {
                method:'POST',
                headers:{'X-Admin-Token':senha},
                body:fd
              });
              if(r.ok){
                const j = await r.json();
                status.innerHTML += '<p class="ok">✅ '+ j.filename +' enviado.</p>';
              }else{
                const e = await r.text();
                status.innerHTML += '<p class="err">❌ '+arquivos[i].name+': '+e+'</p>';
              }
            }catch(err){
              status.innerHTML += '<p class="err">❌ '+arquivos[i].name+': '+err.message+'</p>';
            }
          }
        }
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# Inclui o router sem interferir no resto do app
app.include_router(router)
# ==================== FIM DO BLOCO DE UPLOAD (ADMIN) ====================
