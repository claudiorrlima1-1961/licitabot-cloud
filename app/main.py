from fastapi import (
    FastAPI, Request, UploadFile, File, Header,
    HTTPException, Depends, Response
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

# --- # ======================
# # === INÍCIO: BLOCO DE UPLOAD (COLE NO FINAL DO ARQUIVO) ======================
import os
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Header, HTTPException
from fastapi.responses import HTMLResponse

# Router próprio (evita conflito com o resto do seu app)
router = APIRouter()

# Senha para upload (defina no Render em "Environment" como ADMIN_UPLOAD_TOKEN)
ADMIN_UPLOAD_TOKEN = os.getenv("ADMIN_UPLOAD_TOKEN", "admin123")

# Pasta segura no Render. /tmp é sempre gravável
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/uploads")

def _index_pdf_placeholder(file_path: str) -> int:
    """
    Placeholder seguro:
      - Confirma que o arquivo existe.
      - Lê 1KB apenas para validar acesso.
      - (Depois você pode trocar por sua indexação RAG real.)
    """
    if not os.path.exists(file_path):
        raise RuntimeError(f"Arquivo não encontrado após upload: {file_path}")
    with open(file_path, "rb") as fh:
        _ = fh.read(1024)
    return 1  # simulando 1 chunk indexado

@router.post("/upload_pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None, convert_underscores=False)
):
    # 1) Segurança: checa token do admin
    if not x_admin_token or x_admin_token != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token incorreto")

    # 2) Aceita apenas PDF
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Envie apenas arquivos .pdf")

    # 3) Salvar com robustez em /tmp/uploads
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        save_path = os.path.join(UPLOAD_DIR, file.filename)

        content = await file.read()
        with open(save_path, "wb") as out:
            out.write(content)

        if not os.path.exists(save_path):
            raise RuntimeError("Arquivo não foi gravado corretamente no servidor.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao salvar PDF: {e}")

    # 4) Indexar (placeholder seguro)
    try:
        chunks = _index_pdf_placeholder(save_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao indexar PDF: {e}")

    # 5) OK
    return {"status": "ok", "filename": file.filename, "chunks": chunks, "path": save_path}

@router.get("/upload", response_class=HTMLResponse)
async def upload_page():
    """
    Telinha simples de upload. A checagem real é no /upload_pdf (via header X-Admin-Token).
    """
    html = """
    <!doctype html>
    <html lang="pt-BR">
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <title>Upload de PDFs — Licitabot</title>
      <style>
        :root { --bg:#0f2f3d; --card:#ffffff; --btn:#0b3d5c; --txt:#1f2937; }
        html,body{height:100%; margin:0}
        body{font-family:Arial,Helvetica,sans-serif; background:#f4f6f8; color:var(--txt); display:flex; align-items:center; justify-content:center; padding:24px;}
        .card{width:100%; max-width:440px; background:#fff; border-radius:16px; box-shadow:0 10px 30px rgba(0,0,0,.12); padding:24px;}
        h1{margin:0 0 8px; font-size:22px; color:#0b2942;}
        .sub{margin:0 0 16px; font-size:14px; color:#475569;}
        input,button{width:100%; box-sizing:border-box;}
        input[type=password],input[type=file]{margin:8px 0 12px; padding:10px; border:1px solid #cbd5e1; border-radius:10px;}
        button{background:var(--btn); color:#fff; border:none; padding:12px; border-radius:10px; font-weight:600; cursor:pointer;}
        button:hover{filter:brightness(1.05);}
        #status{margin-top:12px; font-size:14px; max-height:240px; overflow:auto;}
        .ok{color:#166534} .err{color:#991b1b}
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Envio de PDFs (Admin)</h1>
        <p class="sub">Digite a senha de administrador e selecione um ou mais PDFs.</p>
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
          if(!senha){ status.innerHTML = '<p class="err">Informe a senha.</p>'; return; }
          if(!arquivos.length){ status.innerHTML = '<p class="err">Selecione ao menos 1 PDF.</p>'; return; }

          for(let i=0;i<arquivos.length;i++){
            const fd = new FormData();
            fd.append('file', arquivos[i]);

            try{
              const r = await fetch('/upload_pdf', {
                method:'POST',
                headers: { 'X-Admin-Token': senha },
                body: fd
              });

              if(r.ok){
                const j = await r.json();
                status.innerHTML += '<p class="ok">✅ '+ j.filename +' enviado.</p>';
              }else{
                const t = await r.text();
                status.innerHTML += '<p class="err">❌ '+ arquivos[i].name +': '+ t +'</p>';
              }
            }catch(e){
              status.innerHTML += '<p class="err">❌ '+ arquivos[i].name +': '+ e.message +'</p>';
            }
          }
        }
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# Inclui o router no seu app existente (não altera o restante do sistema)
try:
    app.include_router(router)
except NameError:
    # Se seu app principal tiver outro nome, ajuste aqui:
    from fastapi import FastAPI
    _fallback_app = FastAPI()
    _fallback_app.include_router(router)
# === FIM: BLOCO DE UPLOAD =====================================================
