# -*- coding: utf-8 -*-
import os
import time
import hmac
import hashlib
import logging
from typing import Optional

from fastapi import (
    FastAPI, Request, UploadFile, File, Header, HTTPException,
    Response, APIRouter, BackgroundTasks
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse, FileResponse

# ==================== CONFIGURAÇÃO INICIAL SEGURA ====================
app = FastAPI(title="Licitabot – Cloud")

# Configuração de logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("licitabot")

# ==================== IMPORTS SEGUROS ====================
RAG_AVAILABLE = False
try:
    # Tenta importar os módulos RAG
    from rag_store import ingest_paths, search, context_from_hits
    from core import answer
    RAG_AVAILABLE = True
    log.info("✅ Módulos RAG carregados com sucesso")
except ImportError as e:
    log.warning(f"⚠️ Módulos RAG não disponíveis: {e}")
    # Funções placeholder
    def ingest_paths(paths): 
        log.info(f"📚 Indexação simulada para: {paths}")
    def search(query, k=4): 
        return []
    def context_from_hits(hits): 
        return ""
    def answer(question, context): 
        return "Sistema de pesquisa em manutenção. Tente novamente mais tarde."

# ==================== VARIÁVEIS DE AMBIENTE ====================
ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "1234").strip()
ADMIN_UPLOAD_TOKEN = os.getenv("ADMIN_UPLOAD_TOKEN", "admin123").strip()
SECRET_KEY = os.getenv("SECRET_KEY", "seguro-troque-isso").strip()

# ==================== SISTEMA DE SESSÃO ====================
SESSION_COOKIE = "licita_sess"
SESSION_TTL = 60 * 60 * 24 * 7

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
        return hmac.compare_digest(expected, sig) and int(exp) >= time.time()
    except Exception:
        return False

# ==================== ROTAS BÁSICAS ====================
@app.get("/")
async def root():
    return {"status": "online", "rag": RAG_AVAILABLE}

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "1.0"}

@app.get("/login")
async def login_page():
    """Página de login simples"""
    html = """
    <html>
    <body style="font-family: Arial; text-align: center; padding: 50px;">
        <h2>🔐 Licitabot - Login</h2>
        <form action="/login" method="post">
            <input type="password" name="password" placeholder="Senha" style="padding: 10px; margin: 10px;">
            <br>
            <button type="submit" style="padding: 10px 20px; background: #0b3d5c; color: white; border: none;">
                Entrar
            </button>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(html)

@app.post("/login")
async def login(request: Request):
    form = await request.form()
    password = form.get("password", "").strip()
    
    if password == ACCESS_PASSWORD:
        token = make_token()
        response = RedirectResponse(url="/chat", status_code=302)
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True)
        return response
    else:
        return HTMLResponse("❌ Senha incorreta. <a href='/login'>Tentar novamente</a>")

@app.get("/chat")
async def chat_page(request: Request):
    """Página do chat"""
    token = request.cookies.get(SESSION_COOKIE)
    if not verify_token(token):
        return RedirectResponse(url="/login", status_code=302)
    
    html = """
    <html>
    <body style="font-family: Arial; max-width: 800px; margin: 0 auto; padding: 20px;">
        <h2>💬 Licitabot - Chat</h2>
        <div>
            <input type="text" id="pergunta" placeholder="Digite sua pergunta..." style="width: 70%; padding: 10px;">
            <button onclick="perguntar()" style="padding: 10px 20px;">Perguntar</button>
        </div>
        <div id="resposta" style="margin-top: 20px; padding: 15px; border: 1px solid #ccc; min-height: 100px;">
            Faça uma pergunta sobre seus documentos...
        </div>
        
        <script>
        async function perguntar() {
            const pergunta = document.getElementById('pergunta').value;
            const resposta = document.getElementById('resposta');
            
            if (!pergunta) return;
            
            resposta.innerHTML = "🔍 Pesquisando...";
            
            try {
                const response = await fetch('/ask', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({question: pergunta})
                });
                
                const data = await response.json();
                resposta.innerHTML = data.answer || "❌ Erro na resposta";
            } catch (error) {
                resposta.innerHTML = "❌ Erro de conexão: " + error.message;
            }
        }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ==================== SISTEMA DE UPLOAD ====================
upload_router = APIRouter()
UPLOAD_DIR = "uploaded_pdfs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@upload_router.post("/upload_pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    x_admin_token: Optional[str] = Header(None),
    background_tasks: BackgroundTasks = None
):
    """Upload simples de PDF"""
    log.info(f"📤 Recebendo arquivo: {file.filename}")
    
    # Verifica token
    if not x_admin_token or x_admin_token.strip() != ADMIN_UPLOAD_TOKEN:
        raise HTTPException(401, "Token de administrador inválido")
    
    # Verifica se é PDF
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(422, "Apenas arquivos PDF são permitidos")
    
    # Salva o arquivo
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    
    try:
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        log.info(f"✅ Arquivo salvo: {file.filename} ({len(content)} bytes)")
        
        # Indexa em background se RAG disponível
        if RAG_AVAILABLE and background_tasks:
            background_tasks.add_task(ingest_paths, [file_path])
        
        return {
            "status": "success", 
            "filename": file.filename,
            "size": len(content),
            "indexed": RAG_AVAILABLE
        }
        
    except Exception as e:
        log.error(f"❌ Erro no upload: {e}")
        raise HTTPException(500, f"Erro ao salvar arquivo: {str(e)}")

@upload_router.get("/upload")
async def upload_page():
    """Página de upload"""
    html = """
    <html>
    <body style="font-family: Arial; max-width: 500px; margin: 0 auto; padding: 20px;">
        <h2>📤 Upload de PDFs</h2>
        <p>Envie arquivos PDF para o sistema</p>
        
        <input type="password" id="token" placeholder="Token de admin" style="width: 100%; padding: 10px; margin: 10px 0;">
        <input type="file" id="arquivo" accept=".pdf" style="margin: 10px 0;">
        <button onclick="upload()" style="padding: 10px 20px; width: 100%;">Enviar PDF</button>
        
        <div id="status" style="margin-top: 20px;"></div>
        
        <script>
        async function upload() {
            const token = document.getElementById('token').value;
            const arquivo = document.getElementById('arquivo').files[0];
            const status = document.getElementById('status');
            
            if (!token || !arquivo) {
                status.innerHTML = "❌ Preencha o token e selecione um arquivo";
                return;
            }
            
            const formData = new FormData();
            formData.append('file', arquivo);
            
            status.innerHTML = "📤 Enviando...";
            
            try {
                const response = await fetch('/upload_pdf', {
                    method: 'POST',
                    headers: {'X-Admin-Token': token},
                    body: formData
                });
                
                if (response.ok) {
                    const data = await response.json();
                    status.innerHTML = `✅ ${data.filename} enviado com sucesso!`;
                } else {
                    const error = await response.text();
                    status.innerHTML = `❌ Erro: ${error}`;
                }
            } catch (error) {
                status.innerHTML = `❌ Erro de conexão: ${error.message}`;
            }
        }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ==================== ROTA DE CONSULTA ====================
@app.post("/ask")
async def ask_question(request: Request, payload: dict = None):
    """Endpoint de consulta"""
    # Verifica autenticação
    token = request.cookies.get(SESSION_COOKIE)
    if not verify_token(token):
        raise HTTPException(401, "Não autorizado")
    
    if not payload:
        payload = {}
    
    question = payload.get("question", "").strip()
    if not question:
        return {"answer": "Por favor, digite uma pergunta"}
    
    if RAG_AVAILABLE:
        try:
            hits = search(question, k=3)
            if hits:
                context = context_from_hits(hits)
                resposta = answer(question, context)
                return {"answer": resposta}
            else:
                return {"answer": "Não encontrei informações relevantes nos documentos"}
        except Exception as e:
            log.error(f"Erro no RAG: {e}")
            return {"answer": "Erro no sistema de pesquisa"}
    else:
        return {"answer": "Sistema de pesquisa temporariamente indisponível"}

# Conecta o router de upload
app.include_router(upload_router)

# ==================== INICIALIZAÇÃO ====================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
