-- coding: utf-8 --

import os
import time
import hmac
import pathlib
import hashlib
import logging
from typing import Optional, List, Tuple, Dict

from fastapi import FastAPI, Request, UploadFile, File, Header, Depends, Response, HTTPException, APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

---- Suas dependências de RAG (mantidas) ------------------------------------

from .rag_store import ingest_paths, search, context_from_hits
from .core import answer

-----------------------------------------------------------------------------

app = FastAPI(title="Licitabot – Cloud")

Pastas de assets

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

-------------------- Variáveis de ambiente (com .strip) ---------------------

ACCESS_PASSWORD = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()

Unificação: usa ADMIN_UPLOAD_TOKEN (se existir) ou ADMIN_TOKEN; fallback para admin123

ADMIN_UPLOAD_TOKEN = (os.getenv("ADMIN_UPLOAD_TOKEN") or os.getenv("ADMIN_TOKEN") or "admin123").strip()
ADMIN_TOKEN = ADMIN_UPLOAD_TOKEN  # compatível com /ask
SECRET_KEY = (os.getenv("SECRET_KEY", "troque-este-segredo") or "troque-este-segredo").strip()

----------------------------- Sessão simples --------------------------------

SESSION_COOKIE = "licita_sess"
SESSION_TTL = 60 * 60 * 24 * 7  # 7 dias

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

------------------------------- Páginas --------------------------------------

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
resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax")
return resp

@app.get("/chat", response_class=HTMLResponse)
def page_chat(request: Request):
token = request.cookies.get(SESSION_COOKIE)
if not token or not verify_token(token):
return RedirectResponse(url="/", status_code=302)
return templates.TemplateResponse("index.html", {"request": request})

------------------------------- Chat (/ask) ----------------------------------

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

# Se enviar o header X-Admin-Token válido, devolve também citações  
if (x_admin_token or "").strip() == ADMIN_TOKEN:  
    return {  
        "answer": ans,  
        "citations": [  
            {"source": md.get("source"), "chunk": md.get("chunk"), "excerpt": doc[:280]}  
            for (doc, md) in hits  
        ]  
    }  
return {"answer": ans}

====================== BLOCO DE UPLOAD (ADMIN) ==============================

log = logging.getLogger("upload")
log.setLevel(logging.INFO)

router = APIRouter()

Pasta de destino dentro do app (mantida no repositório com .gitkeep)

UPLOAD_DIR = os.path.join(os.path.dirname(file), "uploaded_pdfs")

def _verifica_gravacao(caminho: str) -> None:
if not os.path.exists(caminho):
raise RuntimeError("Arquivo não foi encontrado após o upload.")
with open(caminho, "rb") as fh:
_ = fh.read(1024)  # valida leitura

@router.post("/upload_pdf", response_class=JSONResponse)
async def upload_pdf(
file: UploadFile = File(...),
x_admin_token: Optional[str] = Header(None),  # ← correção: remove convert_underscores=False
):
# Segurança do admin (tolera espaços acidentais)
if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
raise HTTPException(status_code=401, detail="Token de administrador inválido.")

# Somente PDF  
if not file.filename.lower().endswith(".pdf"):  
    raise HTTPException(status_code=422, detail="Envie apenas arquivos .pdf")  

# Salvar e validar  
try:  
    os.makedirs(UPLOAD_DIR, exist_ok=True)  
    destino = os.path.join(UPLOAD_DIR, file.filename)  

    # grava em chunks de 1MB  
    with open(destino, "wb") as buffer:  
        while True:  
            chunk = await file.read(1024 * 1024)  
            if not chunk:  
                break  
            buffer.write(chunk)  

    _verifica_gravacao(destino)  
    log.info(f"[UPLOAD] PDF salvo: {destino}")  

    # Indexa no seu RAG (opcional, mas já integrado)  
    try:  
        ingest_paths([destino])  
    except Exception as ie:  
        log.exception("Falha ao indexar PDF")  
        # não derruba o upload; apenas informa  
        return {"status": "ok", "filename": file.filename, "saved_to": f"app/uploaded_pdfs/{file.filename}", "index_error": str(ie)}  

except Exception as e:  
    log.exception("Falha ao salvar PDF")  
    raise HTTPException(status_code=500, detail=f"Falha ao salvar PDF: {type(e).__name__} - {e}")  

return {"status": "ok", "filename": file.filename, "saved_to": f"app/uploaded_pdfs/{file.filename}"}

@router.get("/upload", response_class=HTMLResponse)
async def upload_page():
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
      const senha = document.getElementById('senha').value.trim(); // ← correção: tira espaços  
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

Inclui o router do upload

app.include_router(router)

==================== FIM DO BLOCO DE UPLOAD (ADMIN) =========================

==================== TESTE DE TOKEN (DIAGNÓSTICO SIMPLES) ====================

@app.get("/testar_token", response_class=HTMLResponse)
async def testar_token_page():
html = """
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Teste de Token — Licitabot</title>
<style>
body{font-family:Arial,Helvetica,sans-serif;background:#f4f6f8;color:#1f2937;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}
.card{background:#fff;padding:24px;border-radius:16px;box-shadow:0 8px 20px rgba(0,0,0,.1);
max-width:360px;width:100%;text-align:center;}
input{width:100%;padding:10px;margin:12px 0;border:1px solid #ccc;border-radius:8px;}
button{width:100%;background:#0b3d5c;color:#fff;border:none;padding:10px;border-radius:8px;cursor:pointer;}
button:hover{filter:brightness(1.05)}
#msg{margin-top:16px;font-weight:600;}
.ok{color:#166534}.err{color:#991b1b}
</style>
</head>
<body>
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
const msg = document.getElementById('msg');
msg.innerHTML = '';
try{
const r = await fetch('/check_token', {headers:{'X-Admin-Token':senha}});
const t = await r.text();
if(r.ok){ msg.innerHTML = '<p class="ok">'+t+'</p>'; }
else{ msg.innerHTML = '<p class="err">'+t+'</p>'; }
}catch(e){
msg.innerHTML = '<p class="err">Erro de conexão</p>';
}
}
</script>
</body>
</html>
"""
return HTMLResponse(html)

@app.get("/check_token", response_class=PlainTextResponse)
async def check_token(x_admin_token: Optional[str] = Header(None)):
if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
return PlainTextResponse("✅ Token válido", status_code=200)
else:
return PlainTextResponse("❌ Token inválido", status_code=401)

------------------------------ Uvicorn (Render) -----------------------------

if name == "main":
import uvicorn
port = int(os.environ.get("PORT", 10000))
uvicorn.run("app.main:app", host="0.0.0.0", port=port)

estou enviando todo. Pode alterar?
