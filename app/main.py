# -*- coding: utf-8 -*-
import os
import time
import hmac
import pathlib
import hashlib
import logging
from typing import Optional, List, Tuple, Dict

from fastapi import (
    FastAPI, Request, UploadFile, File, Header, Depends,
    Response, HTTPException, APIRouter, BackgroundTasks
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---- Suas dependências de RAG (mantidas) ------------------------------------
try:
    from .rag_store import ingest_paths, search, context_from_hits
    from .core import answer
    RAG_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ Aviso: Módulos RAG não disponíveis - {e}")
    RAG_AVAILABLE = False
    # Funções dummy para evitar erros
    def ingest_paths(paths): pass
    def search(query, k=4): return []
    def context_from_hits(hits): return ""
    def answer(question, context): return "Sistema RAG não disponível no momento."

# -----------------------------------------------------------------------------
app = FastAPI(title="Licitabot – Cloud")

# Pastas de assets
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
    templates = Jinja2Templates(directory="templates")
except Exception as e:
    print(f"⚠️ Aviso: Assets não carregados - {e}")

# -------------------- Variáveis de ambiente ---------------------
ACCESS_PASSWORD = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()
ADMIN_UPLOAD_TOKEN = (os.getenv("ADMIN_UPLOAD_TOKEN") or os.getenv("ADMIN_TOKEN") or "admin123").strip()
ADMIN_TOKEN = ADMIN_UPLOAD_TOKEN
SECRET_KEY = (os.getenv("SECRET_KEY", "troque-este-segredo") or "troque-este-segredo").strip()

# ----------------------------- Sessão simples --------------------------------
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

# ------------------------------- Páginas --------------------------------------
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

# ------------------------------- Chat (/ask) ----------------------------------
@app.post("/ask")
async def ask(
    payload: dict,
    request: Request,
    x_admin_token: Optional[str] = Header(None)
):
    # Verifica autenticação
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not verify_token(token):
        raise HTTPException(status_code=401, detail="Acesso não autorizado.")
    
    q = (payload or {}).get("question", "").strip()
    if not q:
        return {"answer": "Por favor, escreva sua pergunta."}

    # Usa RAG se disponível
    if RAG_AVAILABLE:
        hits = search(q, k=4)
        if not hits:
            return {"answer": "Não encontrei essa informação na base de documentos."}

        ctx = context_from_hits(hits)
        try:
            ans = answer(q, ctx)
        except Exception as e:
            ans = f"Erro ao consultar o modelo: {e}"
    else:
        ans = "⚠️ Sistema de pesquisa temporariamente indisponível. Recarregue a página."

    # Se for admin, mostra citações
    if (x_admin_token or "").strip() == ADMIN_TOKEN and RAG_AVAILABLE:
        return {
            "answer": ans,
            "citations": [
                {"source": md.get("source"), "chunk": md.get("chunk"), "excerpt": doc[:280]}
                for (doc, md) in hits
            ]
        }
    return {"answer": ans}

# ====================== BLOCO DE UPLOAD E GERENCIAMENTO DE PDFs ==============
log = logging.getLogger("upload")
log.setLevel(logging.INFO)

router = APIRouter()

# Pasta de uploads (criada automaticamente)
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploaded_pdfs")
os.makedirs(UPLOAD_DIR, exist_ok=True)  # Garante que a pasta existe

# Limite de tamanho para evitar timeout
MAX_UPLOAD_MB = 50
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

def _verifica_gravacao(caminho: str) -> None:
    """Verifica se o arquivo foi salvo corretamente"""
    if not os.path.exists(caminho):
        raise RuntimeError("Arquivo não foi encontrado após o upload.")
    with open(caminho, "rb") as fh:
        _ = fh.read(1024)  # Testa leitura

async def _indexar_em_background(destino: str):
    """Indexa o PDF em segundo plano (se RAG disponível)"""
    if not RAG_AVAILABLE:
        log.warning("RAG não disponível para indexação")
        return
        
    try:
        ingest_paths([destino])
        log.info(f"✅ PDF indexado: {os.path.basename(destino)}")
    except Exception as e:
        log.error(f"❌ Falha ao indexar {destino}: {e}")

@router.post("/upload_pdf", response_class=JSONResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None),
    background_tasks: BackgroundTasks = None,
):
    """Faz upload de PDF (somente admin)"""
    print(f"📤 Iniciando upload: {file.filename}")
    
    # 1) Verifica token admin
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        print(f"❌ Token inválido: {x_admin_token}")
        raise HTTPException(status_code=401, detail="Token de administrador inválido.")
    
    print("✅ Token válido")

    # 2) Verifica se é PDF
    nome = (file.filename or "").strip()
    if not nome.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Envie apenas arquivos .pdf")

    # 3) Salva o arquivo
    try:
        destino = os.path.join(UPLOAD_DIR, nome)
        print(f"💾 Salvando em: {destino}")

        # Verifica se arquivo já existe
        if os.path.exists(destino):
            raise HTTPException(status_code=409, detail="Arquivo com este nome já existe.")

        total = 0
        with open(destino, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB por chunk
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    buffer.close()
                    os.remove(destino)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Arquivo excede {MAX_UPLOAD_MB} MB"
                    )
                buffer.write(chunk)

        # Verifica se salvou corretamente
        _verifica_gravacao(destino)
        print(f"✅ Upload concluído: {nome} ({total} bytes)")

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Erro no upload: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao salvar: {str(e)}")

    # 4) Indexa em segundo plano
    if background_tasks and RAG_AVAILABLE:
        background_tasks.add_task(_indexar_em_background, destino)
        indexed_msg = "Indexação em andamento..."
    else:
        indexed_msg = "Upload concluído (sem indexação)"

    return {
        "status": "success",
        "filename": nome,
        "size": total,
        "indexed": RAG_AVAILABLE,
        "message": indexed_msg
    }

@router.get("/upload", response_class=HTMLResponse)
async def upload_page():
    """Página de upload de PDFs"""
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
        .card{width:100%;max-width:500px;background:#fff;border-radius:16px;
              box-shadow:0 10px 28px rgba(0,0,0,.12);padding:24px;}
        h1{margin:0 0 8px;font-size:22px;color:#0b2942}
        .sub{margin:0 0 16px;font-size:14px;color:var(--muted)}
        input,button{width:100%;box-sizing:border-box}
        input[type=password],input[type=file]{margin:8px 0 12px;padding:10px;border:1px solid #cbd5e1;border-radius:10px}
        button{background:var(--btn);color:#fff;border:none;padding:12px;border-radius:10px;font-weight:600;cursor:pointer}
        button:hover{filter:brightness(1.05)}
        #status{margin-top:12px;font-size:14px;max-height:240px;overflow:auto}
        .ok{color:#166534}.err{color:#991b1b}
        .nav{display:flex;gap:10px;margin-bottom:20px;}
        .nav-btn{padding:8px 16px;background:#e2e8f0;border-radius:8px;text-decoration:none;color:#374151;}
        .nav-btn:hover{background:#cbd5e1;}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="nav">
          <a href="/upload" class="nav-btn">📤 Upload</a>
          <a href="/pdfs" class="nav-btn">📁 Ver PDFs</a>
          <a href="/chat" class="nav-btn">💬 Chat</a>
        </div>
        
        <h1>Envio de PDFs (Admin)</h1>
        <p class="sub">Digite a senha do admin e selecione os PDFs</p>
        
        <input id="senha" type="password" placeholder="Senha do admin (ADMIN_UPLOAD_TOKEN)"/>
        <input id="arquivo" type="file" accept="application/pdf" multiple/>
        <button onclick="enviar()">Enviar PDFs</button>
        
        <div id="status"></div>
      </div>

      <script>
        async function enviar(){
          const senha = document.getElementById('senha').value.trim();
          const arquivos = document.getElementById('arquivo').files;
          const status = document.getElementById('status');
          status.innerHTML = '';
          
          if(!senha){ 
            status.innerHTML='<p class="err">❌ Informe a senha.</p>'; 
            return; 
          }
          if(!arquivos.length){ 
            status.innerHTML='<p class="err">❌ Selecione ao menos 1 PDF.</p>'; 
            return; 
          }

          for(let i=0; i<arquivos.length; i++){
            const fd = new FormData(); 
            fd.append('file', arquivos[i]);
            
            try{
              status.innerHTML += `<p>📤 Enviando ${arquivos[i].name}...</p>`;
              
              const r = await fetch('/upload_pdf', {
                method: 'POST',
                headers: {'X-Admin-Token': senha},
                body: fd
              });
              
              if(r.ok){
                const j = await r.json();
                status.innerHTML += `<p class="ok">✅ ${j.filename} - ${j.message}</p>`;
              } else {
                const erro = await r.text();
                status.innerHTML += `<p class="err">❌ ${arquivos[i].name}: ${erro}</p>`;
              }
            } catch(err) {
              status.innerHTML += `<p class="err">❌ ${arquivos[i].name}: ${err.message}</p>`;
            }
          }
        }
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

@router.get("/pdfs", response_class=HTMLResponse)
async def list_pdfs_page():
    """Página para ver e gerenciar PDFs enviados"""
    try:
        files = []
        for filename in os.listdir(UPLOAD_DIR):
            if filename.lower().endswith('.pdf'):
                file_path = os.path.join(UPLOAD_DIR, filename)
                file_size = os.path.getsize(file_path)
                files.append({
                    "name": filename,
                    "size": file_size,
                    "size_mb": round(file_size / (1024 * 1024), 2)
                })
    except Exception as e:
        files = []
        error = str(e)

    html = f"""
    <!doctype html>
    <html lang="pt-BR">
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <title>PDFs Enviados — Licitabot</title>
      <style>
        body{{font-family:Arial,Helvetica,sans-serif;background:#f5f7fa;color:#1f2937;
              margin:0; padding:20px;}}
        .container{{max-width:800px; margin:0 auto; background:#fff; 
                   border-radius:16px; padding:24px; box-shadow:0 10px 28px rgba(0,0,0,.12);}}
        h1{{color:#0b2942; margin-bottom:10px;}}
        .nav{{display:flex; gap:10px; margin-bottom:20px;}}
        .nav-btn{{padding:8px 16px; background:#e2e8f0; border-radius:8px; 
                  text-decoration:none; color:#374151;}}
        .nav-btn:hover{{background:#cbd5e1;}}
        .file-list{{margin-top:20px;}}
        .file-item{{padding:12px; border:1px solid #e2e8f0; border-radius:8px; 
                   margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;}}
        .file-actions{{display:flex; gap:10px;}}
        .btn{{padding:6px 12px; border-radius:6px; text-decoration:none; font-size:14px;}}
        .btn-download{{background:#0b3d5c; color:white;}}
        .btn-delete{{background:#dc2626; color:white;}}
        .empty{{text-align:center; color:#6b7280; padding:40px;}}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="nav">
          <a href="/upload" class="nav-btn">📤 Upload</a>
          <a href="/pdfs" class="nav-btn">📁 Ver PDFs</a>
          <a href="/chat" class="nav-btn">💬 Chat</a>
        </div>
        
        <h1>📁 PDFs Enviados</h1>
        <p>Total de arquivos: {len(files)}</p>
        
        <div class="file-list">
          {"".join([f'''
          <div class="file-item">
            <div>
              <strong>{f['name']}</strong><br>
              <small>{f['size_mb']} MB</small>
            </div>
            <div class="file-actions">
              <a href="/download_pdf/{f['name']}" class="btn btn-download">⬇️ Download</a>
              <a href="/delete_pdf/{f['name']}" class="btn btn-delete" 
                 onclick="return confirm('Excluir {f['name']}?')">🗑️ Excluir</a>
            </div>
          </div>
          ''' for f in files]) if files else '<div class="empty">Nenhum PDF enviado ainda</div>'}
        </div>
      </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@router.get("/download_pdf/{filename}")
async def download_pdf(filename: str):
    """Faz download de um PDF"""
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type='application/pdf'
    )

@router.get("/delete_pdf/{filename}")
async def delete_pdf(filename: str, x_admin_token: Optional[str] = Header(None)):
    """Exclui um PDF (somente admin)"""
    # Verifica token
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    
    try:
        os.remove(file_path)
        return {"status": "success", "message": f"Arquivo {filename} excluído"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao excluir: {str(e)}")

# ==================== TESTE DE TOKEN ====================
@app.get("/testar_token", response_class=HTMLResponse)
async def testar_token_page():
    html = """
    <!doctype html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>Teste de Token</title><style>body{font-family:Arial;background:#f4f6f8;color:#1f2937;
    display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}.card{background:#fff;
    padding:24px;border-radius:16px;box-shadow:0 8px 20px rgba(0,0,0,.1);max-width:360px;width:100%;text-align:center;}
    input{width:100%;padding:10px;margin:12px 0;border:1px solid #ccc;border-radius:8px;}button{width:100%;
    background:#0b3d5c;color:#fff;border:none;padding:10px;border-radius:8px;cursor:pointer;}button:hover{filter:brightness(1.05)}
    #msg{margin-top:16px;font-weight:600;}.ok{color:#166534}.err{color:#991b1b}</style></head>
    <body><div class="card"><h2>🔑 Teste de Token</h2><p>Digite a senha do admin</p>
    <input type="password" id="token" placeholder="Senha do admin"/><button onclick="verificar()">Verificar</button>
    <div id="msg"></div></div><script>async function verificar(){const senha=document.getElementById('token').value.trim();
    const msg=document.getElementById('msg');msg.innerHTML='';try{const r=await fetch('/check_token',{headers:{'X-Admin-Token':senha}});
    const t=await r.text();if(r.ok){msg.innerHTML='<p class="ok">'+t+'</p>';}else{msg.innerHTML='<p class="err">'+t+'</p>';}}
    catch(e){msg.innerHTML='<p class="err">Erro de conexão</p>';}}</script></body></html>
    """
    return HTMLResponse(html)

@app.get("/check_token", response_class=PlainTextResponse)
async def check_token(x_admin_token: Optional[str] = Header(None)):
    if (x_admin_token or "").strip() == ADMIN_UPLOAD_TOKEN:
        return PlainTextResponse("✅ Token válido", status_code=200)
    else:
        return PlainTextResponse("❌ Token inválido", status_code=401)

# ==================== HEALTH CHECK ====================
@app.get("/health")
async def health_check():
    """Endpoint de saúde do sistema"""
    return {
        "status": "healthy",
        "upload_dir_exists": os.path.exists(UPLOAD_DIR),
        "pdf_count": len([f for f in os.listdir(UPLOAD_DIR) if f.endswith('.pdf')]) if os.path.exists(UPLOAD_DIR) else 0,
        "rag_available": RAG_AVAILABLE
    }

# Conecta o router de upload ao app principal
app.include_router(router)

# ------------------------------ Uvicorn (Render) -----------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
