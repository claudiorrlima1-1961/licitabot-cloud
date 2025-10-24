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

# ==================== SISTEMA DE SESSÃO SIMPLIFICADO ====================
SESSION_COOKIE = "licita_sess"

def create_session_token():
    """Cria um token de sessão simples"""
    timestamp = str(int(time.time()))
    token_data = f"user_{timestamp}"
    signature = hmac.new(
        SECRET_KEY.encode(), 
        token_data.encode(), 
        hashlib.sha256
    ).hexdigest()
    return f"{token_data}:{signature}"

def verify_session_token(token: str) -> bool:
    """Verifica se o token de sessão é válido"""
    if not token:
        return False
    try:
        token_data, signature = token.split(":", 1)
        expected_signature = hmac.new(
            SECRET_KEY.encode(),
            token_data.encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected_signature, signature)
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
            body { 
                font-family: Arial, sans-serif; 
                max-width: 400px; 
                margin: 100px auto; 
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                height: 100vh;
            }
            .card { 
                background: #fff; 
                padding: 40px; 
                border-radius: 15px; 
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                text-align: center;
            }
            h2 {
                color: #333;
                margin-bottom: 10px;
            }
            .subtitle {
                color: #666;
                margin-bottom: 30px;
            }
            input[type="password"] { 
                width: 100%; 
                padding: 15px; 
                margin: 10px 0; 
                border: 2px solid #e1e5e9; 
                border-radius: 8px;
                font-size: 16px;
                box-sizing: border-box;
            }
            input[type="password"]:focus {
                border-color: #0b3d5c;
                outline: none;
            }
            button { 
                width: 100%; 
                padding: 15px; 
                background: #0b3d5c; 
                color: white; 
                border: none; 
                border-radius: 8px; 
                cursor: pointer;
                font-size: 16px;
                font-weight: bold;
                margin-top: 10px;
            }
            button:hover { 
                background: #0a3350;
                transform: translateY(-2px);
                transition: all 0.2s;
            }
            .password-hint {
                margin-top: 20px;
                padding: 10px;
                background: #f8f9fa;
                border-radius: 5px;
                font-size: 14px;
                color: #666;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <div style="font-size: 48px; margin-bottom: 20px;">🔐</div>
            <h2>Licitabot</h2>
            <p class="subtitle">Sistema de Gestão de Licitações</p>
            
            <form action="/login" method="post">
                <input type="password" name="password" placeholder="Digite a senha de acesso" required>
                <button type="submit">🔓 Entrar no Sistema</button>
            </form>
            
            <div class="password-hint">
                💡 <strong>Senha padrão:</strong> 1234
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
        # Ler dados do formulário
        form_data = await request.form()
        password = form_data.get("password", "").strip()
        
        log.info(f"🔐 Tentativa de login com senha: {password}")
        
        if password == ACCESS_PASSWORD:
            # Cria token de sessão
            token = create_session_token()
            
            # Redireciona para o chat
            response = RedirectResponse(url="/chat", status_code=303)
            response.set_cookie(
                key=SESSION_COOKIE,
                value=token,
                max_age=60*60*24*7,  # 7 dias
                httponly=True,
                samesite="lax"
            )
            
            log.info("✅ Login realizado com sucesso")
            return response
        else:
            log.warning("❌ Tentativa de login com senha incorreta")
            # Retorna página de erro
            html = """
            <html>
            <head>
                <style>
                    body { 
                        font-family: Arial, sans-serif; 
                        text-align: center; 
                        padding: 50px;
                        background: linear-gradient(135deg, #ff6b6b 0%, #ee5a52 100%);
                        height: 100vh;
                        color: white;
                    }
                    .error-card {
                        background: white;
                        color: #333;
                        padding: 40px;
                        border-radius: 15px;
                        box-shadow: 0 10px 30px rgba(0,0,0,0.3);
                        display: inline-block;
                    }
                    .btn {
                        display: inline-block;
                        padding: 12px 24px;
                        background: #0b3d5c;
                        color: white;
                        text-decoration: none;
                        border-radius: 8px;
                        margin-top: 20px;
                    }
                </style>
            </head>
            <body>
                <div class="error-card">
                    <div style="font-size: 48px; color: #dc2626;">❌</div>
                    <h2 style="color: #dc2626;">Senha Incorreta</h2>
                    <p>A senha que você digitou está errada.</p>
                    <a href="/login" class="btn">← Voltar para o Login</a>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(html, status_code=401)
            
    except Exception as e:
        log.error(f"💥 Erro no processo de login: {e}")
        html = f"""
        <html>
        <body style="font-family: Arial; text-align: center; padding: 50px;">
            <h2 style="color: red;">❌ Erro no Sistema</h2>
            <p>Ocorreu um erro inesperado: {str(e)}</p>
            <a href="/login" style="color: #0b3d5c;">← Tentar novamente</a>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=500)

@app.get("/chat")
async def chat_page(request: Request):
    """Página do chat"""
    # Verifica se o usuário está logado
    token = request.cookies.get(SESSION_COOKIE)
    
    if not verify_session_token(token):
        log.warning("🔒 Acesso não autorizado à página do chat")
        return RedirectResponse(url="/login", status_code=302)
    
    log.info("✅ Acesso autorizado ao chat")
    
    html = """
    <html>
    <head>
        <title>Chat - Licitabot</title>
        <style>
            body { 
                font-family: 'Segoe UI', Arial, sans-serif; 
                max-width: 900px; 
                margin: 0 auto; 
                padding: 20px;
                background: #f5f7fa;
            }
            .header { 
                background: linear-gradient(135deg, #0b3d5c 0%, #0a3350 100%); 
                color: white; 
                padding: 30px; 
                border-radius: 15px 15px 0 0; 
                text-align: center;
            }
            .chat-container { 
                background: white;
                border: 1px solid #e1e5e9; 
                border-radius: 0 0 15px 15px; 
                padding: 30px; 
                box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            }
            .input-group {
                display: flex;
                gap: 10px;
                margin-bottom: 20px;
            }
            input[type="text"] { 
                flex: 1; 
                padding: 15px; 
                border: 2px solid #e1e5e9; 
                border-radius: 10px; 
                font-size: 16px;
            }
            input[type="text"]:focus {
                border-color: #0b3d5c;
                outline: none;
            }
            button { 
                padding: 15px 30px; 
                background: #0b3d5c; 
                color: white; 
                border: none; 
                border-radius: 10px; 
                cursor: pointer; 
                font-size: 16px;
                font-weight: bold;
            }
            button:hover { 
                background: #0a3350;
                transform: translateY(-2px);
                transition: all 0.2s;
            }
            #resposta { 
                margin-top: 20px; 
                padding: 20px; 
                border: 2px solid #e2e8f0; 
                border-radius: 10px; 
                min-height: 150px; 
                background: #f8fafc;
                line-height: 1.6;
            }
            .nav { 
                margin-bottom: 20px; 
                display: flex;
                gap: 10px;
            }
            .nav a { 
                padding: 12px 20px; 
                background: white; 
                border: 2px solid #e1e5e9;
                border-radius: 8px; 
                text-decoration: none; 
                color: #374151; 
                font-weight: bold;
            }
            .nav a:hover { 
                background: #0b3d5c; 
                color: white;
                border-color: #0b3d5c;
            }
            .nav a.active {
                background: #0b3d5c;
                color: white;
                border-color: #0b3d5c;
            }
            .loading {
                color: #666;
                font-style: italic;
            }
            .success {
                color: #065f46;
            }
            .error {
                color: #dc2626;
            }
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/chat" class="active">💬 Chat</a>
            <a href="/upload">📤 Upload</a>
            <a href="/pdfs">📁 PDFs</a>
            <a href="/logout">🚪 Sair</a>
        </div>
        
        <div class="header">
            <h1>💬 Licitabot</h1>
            <p>Assistente Inteligente para Licitações</p>
        </div>
        
        <div class="chat-container">
            <div class="input-group">
                <input type="text" id="pergunta" placeholder="Digite sua pergunta sobre licitações, editais, documentos..." autocomplete="off">
                <button onclick="perguntar()">🔍 Perguntar</button>
            </div>
            
            <div id="resposta">
                <div style="text-align: center; color: #666; padding: 40px;">
                    <div style="font-size: 48px; margin-bottom: 20px;">💼</div>
                    <h3>Bem-vindo ao Licitabot!</h3>
                    <p>Faça perguntas sobre seus documentos de licitação e editais.</p>
                    <p><small>Exemplo: "Quais são os requisitos para participar da licitação?"</small></p>
                </div>
            </div>
        </div>
        
        <script>
        async function perguntar() {
            const pergunta = document.getElementById('pergunta').value.trim();
            const resposta = document.getElementById('resposta');
            
            if (!pergunta) {
                resposta.innerHTML = '<div class="error">❌ Por favor, digite uma pergunta</div>';
                return;
            }
            
            resposta.innerHTML = '<div class="loading">🔍 Pesquisando em seus documentos...</div>';
            
            try {
                const response = await fetch('/ask', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({question: pergunta})
                });
                
                if (response.ok) {
                    const data = await response.json();
                    resposta.innerHTML = `<div class="success">${data.answer}</div>`;
                } else {
                    if (response.status === 401) {
                        resposta.innerHTML = '<div class="error">❌ Sessão expirada. <a href="/login">Faça login novamente</a></div>';
                    } else {
                        resposta.innerHTML = '<div class="error">❌ Erro no servidor. Tente novamente.</div>';
                    }
                }
            } catch (error) {
                resposta.innerHTML = `<div class="error">❌ Erro de conexão: ${error.message}</div>`;
            }
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

@app.get("/logout")
async def logout():
    """Faz logout do sistema"""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response

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
async def upload_page(request: Request):
    """Página de upload"""
    # Verifica se está logado
    token = request.cookies.get(SESSION_COOKIE)
    if not verify_session_token(token):
        return RedirectResponse(url="/login", status_code=302)
    
    html = """
    <html>
    <head>
        <title>Upload - Licitabot</title>
        <style>
            body { 
                font-family: 'Segoe UI', Arial, sans-serif; 
                max-width: 600px; 
                margin: 0 auto; 
                padding: 20px;
                background: #f5f7fa;
            }
            .card { 
                background: white; 
                padding: 40px; 
                border-radius: 15px; 
                box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            }
            input, button { 
                width: 100%; 
                padding: 15px; 
                margin: 10px 0; 
                border: 2px solid #e1e5e9; 
                border-radius: 10px; 
                font-size: 16px;
                box-sizing: border-box;
            }
            button { 
                background: #0b3d5c; 
                color: white; 
                border: none; 
                cursor: pointer; 
                font-weight: bold;
            }
            button:hover { 
                background: #0a3350;
                transform: translateY(-2px);
                transition: all 0.2s;
            }
            #status { 
                margin-top: 20px; 
                padding: 20px; 
                border-radius: 10px; 
            }
            .success { 
                background: #d1fae5; 
                color: #065f46; 
                border: 2px solid #a7f3d0; 
            }
            .error { 
                background: #fee2e2; 
                color: #991b1b; 
                border: 2px solid #fecaca; 
            }
            .nav { 
                margin-bottom: 20px; 
                display: flex;
                gap: 10px;
            }
            .nav a { 
                padding: 12px 20px; 
                background: white; 
                border: 2px solid #e1e5e9;
                border-radius: 8px; 
                text-decoration: none; 
                color: #374151; 
                font-weight: bold;
            }
            .nav a:hover { 
                background: #0b3d5c; 
                color: white;
                border-color: #0b3d5c;
            }
            .nav a.active {
                background: #0b3d5c;
                color: white;
                border-color: #0b3d5c;
            }
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/chat">💬 Chat</a>
            <a href="/upload" class="active">📤 Upload</a>
            <a href="/pdfs">📁 PDFs</a>
            <a href="/logout">🚪 Sair</a>
        </div>
        
        <div class="card">
            <h2 style="text-align: center; color: #0b3d5c; margin-bottom: 10px;">📤 Upload de PDFs</h2>
            <p style="text-align: center; color: #666; margin-bottom: 30px;">Envie arquivos PDF para o sistema</p>
            
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
async def list_pdfs_page(request: Request):
    """Página para listar PDFs enviados"""
    # Verifica se está logado
    token = request.cookies.get(SESSION_COOKIE)
    if not verify_session_token(token):
        return RedirectResponse(url="/login", status_code=302)
    
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
            body {{ 
                font-family: 'Segoe UI', Arial, sans-serif; 
                max-width: 800px; 
                margin: 0 auto; 
                padding: 20px;
                background: #f5f7fa;
            }}
            .file-list {{ margin-top: 20px; }}
            .file-item {{ 
                padding: 20px; 
                border: 2px solid #e1e5e9; 
                border-radius: 10px; 
                margin-bottom: 15px; 
                display: flex; 
                justify-content: space-between; 
                align-items: center;
                background: white;
            }}
            .file-actions {{ display: flex; gap: 10px; }}
            .btn {{ 
                padding: 10px 20px; 
                border-radius: 8px; 
                text-decoration: none; 
                font-size: 14px;
                font-weight: bold;
            }}
            .btn-download {{ 
                background: #0b3d5c; 
                color: white; 
            }}
            .btn-download:hover {{
                background: #0a3350;
            }}
            .empty {{ 
                text-align: center; 
                color: #6b7280; 
                padding: 60px;
                background: white;
                border-radius: 10px;
                border: 2px dashed #e1e5e9;
            }}
            .nav {{ 
                margin-bottom: 20px; 
                display: flex;
                gap: 10px;
            }}
            .nav a {{ 
                padding: 12px 20px; 
                background: white; 
                border: 2px solid #e1e5e9;
                border-radius: 8px; 
                text-decoration: none; 
                color: #374151; 
                font-weight: bold;
            }}
            .nav a:hover {{ 
                background: #0b3d5c; 
                color: white;
                border-color: #0b3d5c;
            }}
            .nav a.active {{
                background: #0b3d5c;
                color: white;
                border-color: #0b3d5c;
            }}
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/chat">💬 Chat</a>
            <a href="/upload">📤 Upload</a>
            <a href="/pdfs" class="active">📁 PDFs</a>
            <a href="/logout">🚪 Sair</a>
        </div>
        
        <h2 style="color: #0b3d5c;">📁 PDFs Enviados</h2>
        <p>Total de arquivos: {len(files)}</p>
        
        <div class="file-list">
            {"".join([f'''
            <div class="file-item">
                <div>
                    <strong style="font-size: 16px;">📄 {f['name']}</strong><br>
                    <small style="color: #666;">📏 {f['size_mb']} MB</small>
                </div>
                <div class="file-actions">
                    <a href="/download_pdf/{f['name']}" class="btn btn-download">⬇️ Download</a>
                </div>
            </div>
            ''' for f in files]) if files else '''
            <div class="empty">
                <div style="font-size: 48px; margin-bottom: 20px;">📭</div>
                <h3>Nenhum PDF Enviado</h3>
                <p>Vá para a página de upload para enviar seus primeiros documentos.</p>
                <a href="/upload" style="display: inline-block; margin-top: 20px; padding: 12px 24px; background: #0b3d5c; color: white; text-decoration: none; border-radius: 8px; font-weight: bold;">📤 Fazer Upload</a>
            </div>
            '''}
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@upload_router.get("/download_pdf/{filename}")
async def download_pdf(filename: str, request: Request):
    """Faz download de um PDF"""
    # Verifica se está logado
    token = request.cookies.get(SESSION_COOKIE)
    if not verify_session_token(token):
        raise HTTPException(401, "Não autorizado")
    
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
    if not verify_session_token(token):
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
