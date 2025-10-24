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
    <head>
        <title>Login - Licitabot</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 400px; margin: 100px auto; padding: 20px; }
            .card { background: #fff; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            input[type="password"] { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; }
            button { width: 100%; padding: 12px; background: #0b3d5c; color: white; border: none; border-radius: 5px; cursor: pointer; }
            button:hover { background: #0a3350; }
            .error { color: red; margin-top: 10px; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2 style="text-align: center; color: #0b3d5c;">🔐 Licitabot</h2>
            <p style="text-align: center;">Digite a senha de acesso</p>
            
            <form action="/login" method="post">
                <input type="password" name="password" placeholder="Senha" required>
                <button type="submit">Entrar</button>
            </form>
            
            <div style="text-align: center; margin-top: 20px; font-size: 12px; color: #666;">
                Senha padrão: <strong>1234</strong>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@app.post("/login")
async def login_submit(request: Request):
    """Processa o formulário de login"""
    try:
        form = await request.form()
        password = form.get("password", "").strip()
        
        if password == ACCESS_PASSWORD:
            token = make_token()
            response = RedirectResponse(url="/chat", status_code=303)
            response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax")
            return response
        else:
            # Retorna página de erro
            html = """
            <html>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h2 style="color: red;">❌ Senha incorreta</h2>
                <p>A senha que você digitou está errada.</p>
                <a href="/login" style="color: #0b3d5c;">← Voltar para o login</a>
            </body>
            </html>
            """
            return HTMLResponse(html, status_code=401)
            
    except Exception as e:
        html = f"""
        <html>
        <body style="font-family: Arial; text-align: center; padding: 50px;">
            <h2 style="color: red;">❌ Erro no login</h2>
            <p>Erro: {str(e)}</p>
            <a href="/login" style="color: #0b3d5c;">← Tentar novamente</a>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=500)

@app.get("/chat")
async def chat_page(request: Request):
    """Página do chat"""
    token = request.cookies.get(SESSION_COOKIE)
    if not verify_token(token):
        return RedirectResponse(url="/login", status_code=302)
    
    html = """
    <html>
    <head>
        <title>Chat - Licitabot</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            .header { background: #0b3d5c; color: white; padding: 20px; border-radius: 10px 10px 0 0; }
            .chat-container { border: 1px solid #ddd; border-radius: 0 0 10px 10px; padding: 20px; }
            input[type="text"] { width: 70%; padding: 10px; border: 1px solid #ddd; border-radius: 5px; }
            button { padding: 10px 20px; background: #0b3d5c; color: white; border: none; border-radius: 5px; cursor: pointer; }
            button:hover { background: #0a3350; }
            #resposta { margin-top: 20px; padding: 15px; border: 1px solid #e2e8f0; border-radius: 5px; min-height: 100px; background: #f8fafc; }
            .nav { margin-bottom: 20px; }
            .nav a { padding: 8px 16px; background: #e2e8f0; border-radius: 5px; text-decoration: none; color: #374151; margin-right: 10px; }
            .nav a:hover { background: #cbd5e1; }
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/chat">💬 Chat</a>
            <a href="/upload">📤 Upload</a>
            <a href="/pdfs">📁 PDFs</a>
            <a href="/login" onclick="logout()">🚪 Sair</a>
        </div>
        
        <div class="header">
            <h2>💬 Licitabot - Assistente de Licitações</h2>
        </div>
        
        <div class="chat-container">
            <div>
                <input type="text" id="pergunta" placeholder="Digite sua pergunta sobre os documentos..." style="width: 70%; padding: 10px;">
                <button onclick="perguntar()">Perguntar</button>
            </div>
            <div id="resposta">
                Faça uma pergunta sobre seus documentos de licitação...
            </div>
        </div>
        
        <script>
        async function perguntar() {
            const pergunta = document.getElementById('pergunta').value;
            const resposta = document.getElementById('resposta');
            
            if (!pergunta) {
                resposta.innerHTML = "❌ Por favor, digite uma pergunta";
                return;
            }
            
            resposta.innerHTML = "🔍 Pesquisando em seus documentos...";
            
            try {
                const response = await fetch('/ask', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({question: pergunta})
                });
                
                if (response.ok) {
                    const data = await response.json();
                    resposta.innerHTML = data.answer || "❌ Resposta vazia";
                } else {
                    resposta.innerHTML = "❌ Erro no servidor. Tente novamente.";
                }
            } catch (error) {
                resposta.innerHTML = "❌ Erro de conexão: " + error.message;
            }
        }
        
        function logout() {
            document.cookie = "licita_sess=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
        }
        
        // Enter key support
        document.getElementById('pergunta').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                perguntar();
            }
        });
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
    <head>
        <title>Upload - Licitabot</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px; }
            .card { background: #fff; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            input, button { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; }
            button { background: #0b3d5c; color: white; border: none; cursor: pointer; }
            button:hover { background: #0a3350; }
            #status { margin-top: 20px; padding: 15px; border-radius: 5px; }
            .success { background: #d1fae5; color: #065f46; border: 1px solid #a7f3d0; }
            .error { background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }
            .nav { margin-bottom: 20px; }
            .nav a { padding: 8px 16px; background: #e2e8f0; border-radius: 5px; text-decoration: none; color: #374151; margin-right: 10px; }
            .nav a:hover { background: #cbd5e1; }
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/chat">💬 Chat</a>
            <a href="/upload">📤 Upload</a>
            <a href="/pdfs">📁 PDFs</a>
        </div>
        
        <div class="card">
            <h2 style="text-align: center; color: #0b3d5c;">📤 Upload de PDFs</h2>
            <p style="text-align: center;">Envie arquivos PDF para o sistema</p>
            
            <input type="password" id="token" placeholder="Token de administrador (admin123)" required>
            <input type="file" id="arquivo" accept=".pdf" required>
            <button onclick="upload()">📎 Enviar PDF</button>
            
            <div id="status"></div>
        </div>
        
        <script>
        async function upload() {
            const token = document.getElementById('token').value.trim();
            const arquivo = document.getElementById('arquivo').files[0];
            const status = document.getElementById('status');
            
            if (!token) {
                status.innerHTML = '<div class="error">❌ Digite o token de administrador</div>';
                return;
            }
            
            if (!arquivo) {
                status.innerHTML = '<div class="error">❌ Selecione um arquivo PDF</div>';
                return;
            }
            
            const formData = new FormData();
            formData.append('file', arquivo);
            
            status.innerHTML = '<div>📤 Enviando arquivo...</div>';
            
            try {
                const response = await fetch('/upload_pdf', {
                    method: 'POST',
                    headers: {'X-Admin-Token': token},
                    body: formData
                });
                
                if (response.ok) {
                    const data = await response.json();
                    status.innerHTML = `<div class="success">
                        ✅ <strong>${data.filename}</strong> enviado com sucesso!<br>
                        📏 Tamanho: ${(data.size / 1024 / 1024).toFixed(2)} MB<br>
                        ${data.indexed ? '🔍 Será indexado para pesquisa' : '⚠️ Indexação não disponível'}
                    </div>`;
                } else {
                    const error = await response.text();
                    status.innerHTML = `<div class="error">❌ Erro: ${error}</div>`;
                }
            } catch (error) {
                status.innerHTML = `<div class="error">❌ Erro de conexão: ${error.message}</div>`;
            }
        }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

@upload_router.get("/pdfs")
async def list_pdfs_page():
    """Página para listar PDFs enviados"""
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
    <html>
    <head>
        <title>PDFs - Licitabot</title>
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
            .file-list {{ margin-top: 20px; }}
            .file-item {{ padding: 15px; border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }}
            .file-actions {{ display: flex; gap: 10px; }}
            .btn {{ padding: 8px 16px; border-radius: 5px; text-decoration: none; font-size: 14px; }}
            .btn-download {{ background: #0b3d5c; color: white; }}
            .btn-delete {{ background: #dc2626; color: white; }}
            .empty {{ text-align: center; color: #6b7280; padding: 40px; }}
            .nav {{ margin-bottom: 20px; }}
            .nav a {{ padding: 8px 16px; background: #e2e8f0; border-radius: 5px; text-decoration: none; color: #374151; margin-right: 10px; }}
            .nav a:hover {{ background: #cbd5e1; }}
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/chat">💬 Chat</a>
            <a href="/upload">📤 Upload</a>
            <a href="/pdfs">📁 PDFs</a>
        </div>
        
        <h2>📁 PDFs Enviados</h2>
        <p>Total de arquivos: {len(files)}</p>
        
        <div class="file-list">
            {"".join([f'''
            <div class="file-item">
                <div>
                    <strong>📄 {f['name']}</strong><br>
                    <small>📏 {f['size_mb']} MB</small>
                </div>
                <div class="file-actions">
                    <a href="/download_pdf/{f['name']}" class="btn btn-download">⬇️ Download</a>
                </div>
            </div>
            ''' for f in files]) if files else '<div class="empty">📭 Nenhum PDF enviado ainda</div>'}
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@upload_router.get("/download_pdf/{filename}")
async def download_pdf(filename: str):
    """Faz download de um PDF"""
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(404, "Arquivo não encontrado")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type='application/pdf'
    )

# ==================== ROTA DE CONSULTA ====================
@app.post("/ask")
async def ask_question(request: Request):
    """Endpoint de consulta"""
    # Verifica autenticação
    token = request.cookies.get(SESSION_COOKIE)
    if not verify_token(token):
        raise HTTPException(401, "Não autorizado")
    
    try:
        # Lê o JSON do corpo da requisição
        payload = await request.json()
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
                    return {"answer": "Não encontrei informações relevantes nos documentos. Tente reformular sua pergunta."}
            except Exception as e:
                log.error(f"Erro no RAG: {e}")
                return {"answer": "Erro no sistema de pesquisa. Tente novamente."}
        else:
            return {"answer": "Sistema de pesquisa temporariamente indisponível. Recarregue a página."}
            
    except Exception as e:
        log.error(f"Erro geral em /ask: {e}")
        return {"answer": "Erro interno do servidor. Tente novamente."}

# Conecta o router de upload
app.include_router(upload_router)

# ==================== INICIALIZAÇÃO ====================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
