# -*- coding: utf-8 -*-
import os
import secrets
import logging
from typing import Optional, Dict
from datetime import datetime, timedelta
from fastapi import (
    FastAPI, Request, UploadFile, File, Header, HTTPException,
    APIRouter, BackgroundTasks, Depends, Form
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
import shutil

# ==================== CONFIGURAÇÃO ====================
app = FastAPI(title="Licitabot - Assessoria em Licitações")

# Configuração de logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("licitabot")

# ==================== BANCO DE USUÁRIOS ====================
USERS_DB = {
    "admin": {
        "password": os.getenv("ADMIN_PASSWORD", "admin123"),
        "role": "admin",
        "name": "Administrador"
    },
    "cliente": {
        "password": os.getenv("CLIENT_PASSWORD", "cliente123"), 
        "role": "user",
        "name": "Cliente Premium"
    }
}

# ==================== SISTEMA DE SESSÕES ====================
active_sessions: Dict[str, dict] = {}

def create_session(username: str) -> str:
    session_id = secrets.token_urlsafe(32)
    active_sessions[session_id] = {
        "username": username,
        "role": USERS_DB[username]["role"],
        "name": USERS_DB[username]["name"],
        "created_at": datetime.now()
    }
    return session_id

def verify_session(session_id: str) -> Optional[dict]:
    if not session_id or session_id not in active_sessions:
        return None
    return active_sessions[session_id]

def get_current_user(request: Request):
    session_id = request.cookies.get("licita_session")
    user = verify_session(session_id)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/"})
    return user

def require_admin(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito")
    return user

# ==================== SISTEMA DE ARQUIVOS ====================
UPLOAD_DIR = "uploaded_pdfs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def get_uploaded_files():
    """Lista todos os PDFs enviados"""
    files = []
    for filename in os.listdir(UPLOAD_DIR):
        if filename.lower().endswith('.pdf'):
            file_path = os.path.join(UPLOAD_DIR, filename)
            file_size = os.path.getsize(file_path)
            files.append({
                "name": filename,
                "size": file_size,
                "size_mb": round(file_size / (1024 * 1024), 2),
                "upload_date": datetime.fromtimestamp(os.path.getctime(file_path)).strftime("%d/%m/%Y %H:%M")
            })
    return sorted(files, key=lambda x: x["upload_date"], reverse=True)

# ==================== SISTEMA RAG SIMULADO ====================
RAG_AVAILABLE = False
try:
    from rag_store import ingest_paths, search, context_from_hits
    from core import answer
    RAG_AVAILABLE = True
    log.info("✅ Sistema RAG carregado")
except ImportError as e:
    log.warning(f"⚠️ Sistema RAG não disponível: {e}")
    def ingest_paths(paths): 
        log.info(f"📚 Indexando: {paths}")
        return True
    def search(query, k=4): 
        # Simulação de busca - em produção usa RAG real
        return [("Conteúdo de exemplo do sistema de licitações.", {})]
    def context_from_hits(hits): 
        return " ".join([doc for doc, _ in hits])
    def answer(question, context): 
        respostas_exemplo = {
            "licitação": "O processo licitatório deve seguir a Lei 14.133/2021...",
            "edital": "O edital é o documento que estabelece as regras da licitação...",
            "contrato": "Os contratos administrativos devem conter cláusulas essenciais...",
            "pregão": "O pregão pode ser eletrônico ou presencial, conforme o valor...",
        }
        
        for palavra, resposta in respostas_exemplo.items():
            if palavra in question.lower():
                return resposta
                
        return f"🔍 Com base na análise dos documentos, sobre '{question}':\n\nO sistema de licitações brasileiro é regido principalmente pela Lei 14.133/2021 (Nova Lei de Licitações). Recomenda-se sempre verificar o edital específico e consultar um especialista para casos concretos."

# ==================== PÁGINA DE LOGIN ====================
@app.get("/")
async def login_page():
    html = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Acesso - Licitabot</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: 'Arial', sans-serif;
                background: linear-gradient(135deg, #0b3d5c 0%, #0a3350 100%);
                height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            .login-container {
                background: white;
                padding: 50px;
                border-radius: 20px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.3);
                width: 100%;
                max-width: 450px;
                text-align: center;
            }
            .logo { font-size: 60px; margin-bottom: 20px; color: #0b3d5c; }
            h1 { color: #0b3d5c; margin-bottom: 10px; font-size: 28px; }
            .subtitle { color: #666; margin-bottom: 40px; font-size: 16px; }
            .form-group { margin-bottom: 25px; text-align: left; }
            label { display: block; margin-bottom: 10px; color: #333; font-weight: bold; }
            input[type="password"] {
                width: 100%; padding: 18px; border: 2px solid #e1e5e9; border-radius: 12px;
                font-size: 16px; background: #f8fafc;
            }
            input:focus { border-color: #0b3d5c; outline: none; }
            .login-btn {
                width: 100%; padding: 18px; background: #0b3d5c; color: white;
                border: none; border-radius: 12px; font-size: 16px; font-weight: bold;
                cursor: pointer; transition: all 0.3s; margin-top: 10px;
            }
            .login-btn:hover { background: #0a3350; transform: translateY(-2px); }
            .error-message {
                background: #fee2e2; color: #dc2626; padding: 15px; border-radius: 10px;
                margin-bottom: 25px; display: none;
            }
            .user-options {
                display: flex; gap: 10px; margin: 20px 0;
            }
            .user-btn {
                flex: 1; padding: 12px; background: #f1f5f9; border: 2px solid #e1e5e9;
                border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: bold;
            }
            .user-btn:hover { background: #0b3d5c; color: white; }
            .features {
                display: flex; justify-content: space-around; margin: 30px 0; padding: 25px;
                background: #f0f7ff; border-radius: 12px;
            }
            .feature { text-align: center; }
            .feature-icon { font-size: 24px; margin-bottom: 8px; }
            .feature-text { font-size: 12px; color: #0b3d5c; font-weight: 600; }
            .password-hint {
                margin-top: 25px; padding: 20px; background: #f8f9fa; border-radius: 10px;
                font-size: 12px; color: #666; text-align: left;
            }
        </style>
    </head>
    <body>
        <div class="login-container">
            <div class="logo">💼</div>
            <h1>Licitabot Assessoria</h1>
            <p class="subtitle">Sistema Especializado em Licitações e Contratos Públicos</p>
            
            <div class="features">
                <div class="feature"><div class="feature-icon">🔍</div><div class="feature-text">Pesquisa<br>Inteligente</div></div>
                <div class="feature"><div class="feature-icon">📊</div><div class="feature-text">Análise de<br>Editais</div></div>
                <div class="feature"><div class="feature-icon">⚡</div><div class="feature-text">Respostas<br>Rápidas</div></div>
            </div>
            
            <div id="errorMessage" class="error-message"></div>
            
            <form id="loginForm">
                <div class="form-group">
                    <label for="password">SENHA DE ACESSO:</label>
                    <input type="password" id="password" name="password" required 
                           placeholder="Digite sua senha de assinatura">
                </div>
                
                <button type="submit" class="login-btn" id="loginButton">
                    🔓 ACESSAR SISTEMA
                </button>
            </form>

            <div class="user-options">
                <button class="user-btn" onclick="fillPassword('admin123')">👑 Administrador</button>
                <button class="user-btn" onclick="fillPassword('cliente123')">👤 Cliente</button>
            </div>
            
            <div class="password-hint">
                <strong>🔐 Credenciais:</strong><br><br>
                <strong>Cliente:</strong> cliente123<br>
                <strong>Admin:</strong> admin123<br><br>
                <em>Selecione um botão para preencher automaticamente.</em>
            </div>
        </div>

        <script>
            function fillPassword(password) {
                document.getElementById('password').value = password;
            }

            document.getElementById('loginForm').addEventListener('submit', async function(e) {
                e.preventDefault();
                
                const password = document.getElementById('password').value;
                const errorDiv = document.getElementById('errorMessage');
                const button = document.getElementById('loginButton');
                
                button.innerHTML = '⏳ AUTENTICANDO...';
                button.disabled = true;
                
                try {
                    // Verificação simples - em produção faria request para o servidor
                    if (password === 'admin123' || password === 'cliente123') {
                        button.innerHTML = '✅ ACESSANDO...';
                        setTimeout(() => {
                            window.location.href = '/chat?password=' + encodeURIComponent(password);
                        }, 1000);
                    } else {
                        throw new Error('Senha incorreta');
                    }
                } catch (error) {
                    errorDiv.textContent = '❌ Senha incorreta. Tente novamente.';
                    errorDiv.style.display = 'block';
                    button.innerHTML = '🔓 ACESSAR SISTEMA';
                    button.disabled = false;
                }
            });
            
            document.getElementById('password').focus();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ==================== PÁGINA DO CLIENTE ====================
@app.get("/chat")
async def chat_page(request: Request, password: str = ""):
    """Página de pesquisa do cliente"""
    if password not in ["admin123", "cliente123"]:
        return RedirectResponse(url="/")
    
    user_type = "admin" if password == "admin123" else "cliente"
    
    html = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Pesquisa - Licitabot</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: 'Arial', sans-serif;
                background: #f8fafc;
                color: #333;
            }}
            .navbar {{
                background: #0b3d5c;
                color: white;
                padding: 20px 40px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .nav-links {{ display: flex; gap: 15px; }}
            .nav-links a {{
                color: white;
                text-decoration: none;
                padding: 12px 20px;
                border-radius: 8px;
                transition: background 0.3s;
                font-weight: 600;
            }}
            .nav-links a:hover {{ background: rgba(255,255,255,0.15); }}
            .nav-links a.active {{ background: rgba(255,255,255,0.2); }}
            .container {{ max-width: 900px; margin: 0 auto; padding: 40px 20px; }}
            .chat-header {{
                text-align: center;
                margin-bottom: 40px;
            }}
            .chat-header h1 {{ color: #0b3d5c; font-size: 36px; margin-bottom: 15px; }}
            .chat-header p {{ color: #666; font-size: 18px; }}
            .chat-container {{
                background: white;
                border-radius: 20px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.1);
                overflow: hidden;
            }}
            .chat-messages {{
                height: 500px;
                overflow-y: auto;
                padding: 30px;
                background: #f8fafc;
            }}
            .message {{
                margin-bottom: 25px;
                display: flex;
                gap: 15px;
            }}
            .message.user {{ flex-direction: row-reverse; }}
            .message-avatar {{
                width: 45px;
                height: 45px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: bold;
                flex-shrink: 0;
            }}
            .message.user .message-avatar {{ background: #0b3d5c; color: white; }}
            .message.bot .message-avatar {{ background: #10b981; color: white; }}
            .message-content {{
                max-width: 70%;
                padding: 20px;
                border-radius: 18px;
                line-height: 1.6;
            }}
            .message.user .message-content {{
                background: #0b3d5c;
                color: white;
                border-bottom-right-radius: 5px;
            }}
            .message.bot .message-content {{
                background: white;
                color: #333;
                border: 1px solid #e1e5e9;
                border-bottom-left-radius: 5px;
            }}
            .chat-input-container {{
                padding: 30px;
                background: white;
                border-top: 1px solid #e1e5e9;
            }}
            .input-group {{ display: flex; gap: 15px; }}
            .chat-input {{
                flex: 1;
                padding: 18px 25px;
                border: 2px solid #e1e5e9;
                border-radius: 15px;
                font-size: 16px;
            }}
            .chat-input:focus {{ border-color: #0b3d5c; outline: none; }}
            .send-btn {{
                padding: 18px 35px;
                background: #0b3d5c;
                color: white;
                border: none;
                border-radius: 15px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
            }}
            .send-btn:hover {{ background: #0a3350; }}
            .send-btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
            .examples {{ margin-top: 25px; text-align: center; }}
            .example-buttons {{ display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }}
            .example-btn {{
                padding: 10px 20px;
                background: #f1f5f9;
                color: #475569;
                border: 1px solid #e2e8f0;
                border-radius: 20px;
                font-size: 14px;
                cursor: pointer;
                transition: all 0.3s;
            }}
            .example-btn:hover {{ background: #0b3d5c; color: white; }}
        </style>
    </head>
    <body>
        <div class="navbar">
            <div style="font-size: 24px; font-weight: bold;">💼 Licitabot</div>
            <div class="nav-links">
                <a href="/chat?password={password}" class="active">🔍 Pesquisar</a>
                {"<a href='/upload?password=" + password + "'>📤 Upload</a>" if user_type == "admin" else ""}
                {"<a href='/admin?password=" + password + "'>📁 Gerenciar</a>" if user_type == "admin" else ""}
                <a href="/">🚪 Sair</a>
            </div>
        </div>
        
        <div class="container">
            <div class="chat-header">
                <h1>Assessoria em Licitações</h1>
                <p>Faça perguntas sobre editais, licitações e contratos públicos</p>
            </div>
            
            <div class="chat-container">
                <div class="chat-messages" id="chatMessages">
                    <div class="message bot">
                        <div class="message-avatar">AI</div>
                        <div class="message-content">
                            <strong>Bem-vindo à Assessoria Licitabot! 👋</strong><br><br>
                            Sou especializado em licitações e contratos públicos. Posso ajudar com:
                            <br>• Análise de editais e pregões
                            <br>• Legislação (Lei 14.133/2021)
                            <br>• Contratos administrativos
                            <br>• Jurisprudência do TCU
                            <br><br>
                            <em>Qual sua dúvida sobre licitações?</em>
                        </div>
                    </div>
                </div>
                
                <div class="chat-input-container">
                    <div class="input-group">
                        <input type="text" class="chat-input" id="questionInput" 
                               placeholder="Exemplo: Quais os requisitos para participar de um pregão?" 
                               autocomplete="off">
                        <button class="send-btn" onclick="sendQuestion()" id="sendBtn">
                            🔍 Pesquisar
                        </button>
                    </div>
                    
                    <div class="examples">
                        <div class="example-buttons">
                            <button class="example-btn" onclick="setExample('Quais os tipos de licitação?')">📋 Tipos de licitação</button>
                            <button class="example-btn" onclick="setExample('O que é Lei 14.133/2021?')">⚖️ Nova lei de licitações</button>
                            <button class="example-btn" onclick="setExample('Como funciona o pregão eletrônico?')">💻 Pregão eletrônico</button>
                            <button class="example-btn" onclick="setExample('Quais documentos preciso para licitação?')">📄 Documentação</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            const PASSWORD = '{password}';
            
            function setExample(question) {{
                document.getElementById('questionInput').value = question;
            }}
            
            function addMessage(content, isUser = false) {{
                const chatMessages = document.getElementById('chatMessages');
                const messageDiv = document.createElement('div');
                messageDiv.className = `message ${{isUser ? 'user' : 'bot'}}`;
                
                const avatar = document.createElement('div');
                avatar.className = 'message-avatar';
                avatar.textContent = isUser ? 'VC' : 'AI';
                
                const contentDiv = document.createElement('div');
                contentDiv.className = 'message-content';
                contentDiv.innerHTML = content;
                
                messageDiv.appendChild(avatar);
                messageDiv.appendChild(contentDiv);
                chatMessages.appendChild(messageDiv);
                
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }}
            
            async function sendQuestion() {{
                const question = document.getElementById('questionInput').value.trim();
                if (!question) return;
                
                addMessage(question, true);
                
                const sendBtn = document.getElementById('sendBtn');
                sendBtn.disabled = true;
                sendBtn.innerHTML = '⏳ Pesquisando...';
                
                try {{
                    const response = await fetch('/ask?password=' + PASSWORD, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ question: question }})
                    }});
                    
                    if (response.ok) {{
                        const data = await response.json();
                        addMessage(data.answer);
                    }} else {{
                        addMessage('❌ Erro na pesquisa. Tente novamente.');
                    }}
                }} catch (error) {{
                    addMessage('❌ Erro de conexão. Verifique sua internet.');
                }} finally {{
                    sendBtn.disabled = false;
                    sendBtn.innerHTML = '🔍 Pesquisar';
                    document.getElementById('questionInput').value = '';
                    document.getElementById('questionInput').focus();
                }}
            }}
            
            document.getElementById('questionInput').addEventListener('keypress', function(e) {{
                if (e.key === 'Enter') sendQuestion();
            }});
            
            document.getElementById('questionInput').focus();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ==================== PÁGINA DE UPLOAD (ADMIN) ====================
@app.get("/upload")
async def upload_page(password: str = ""):
    """Página de upload para administrador"""
    if password != "admin123":
        return RedirectResponse(url="/")
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Upload - Licitabot</title>
        <style>
            body {{ 
                font-family: 'Arial', sans-serif;
                max-width: 600px; 
                margin: 0 auto; 
                padding: 20px;
                background: #f8fafc;
            }}
            .card {{ 
                background: white; 
                padding: 40px; 
                border-radius: 15px; 
                box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            }}
            input, button {{ 
                width: 100%; 
                padding: 15px; 
                margin: 12px 0; 
                border: 2px solid #e1e5e9; 
                border-radius: 10px;
                font-size: 16px;
                box-sizing: border-box;
            }}
            button {{ 
                background: #0b3d5c; 
                color: white; 
                border: none; 
                cursor: pointer;
                font-weight: 600;
            }}
            button:hover {{ background: #0a3350; }}
            #status {{ 
                margin-top: 20px; 
                padding: 20px; 
                border-radius: 10px; 
            }}
            .success {{ 
                background: #d1fae5; 
                color: #065f46; 
                border: 2px solid #a7f3d0; 
            }}
            .error {{ 
                background: #fee2e2; 
                color: #991b1b; 
                border: 2px solid #fecaca; 
            }}
            .nav {{ 
                margin-bottom: 25px; 
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
                font-weight: 600;
            }}
            .nav a:hover {{ 
                background: #0b3d5c; 
                color: white;
            }}
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/chat?password={password}">← Voltar ao Chat</a>
            <a href="/admin?password={password}">📁 Gerenciar Arquivos</a>
        </div>
        
        <div class="card">
            <h2 style="text-align: center; color: #0b3d5c;">📤 Upload de Documentos</h2>
            <p style="text-align: center; color: #666; margin-bottom: 30px;">
                Envie PDFs para o sistema de consulta
            </p>
            
            <input type="file" id="arquivo" accept=".pdf" required>
            <button onclick="upload()">📎 Enviar PDF</button>
            
            <div id="status"></div>
        </div>
        
        <script>
        async function upload() {{
            const arquivo = document.getElementById('arquivo').files[0];
            const status = document.getElementById('status');
            
            if (!arquivo) {{
                status.innerHTML = '<div class="error">❌ Selecione um arquivo PDF</div>';
                return;
            }}
            
            const formData = new FormData();
            formData.append('file', arquivo);
            
            status.innerHTML = '<div>📤 Enviando arquivo...</div>';
            
            try {{
                const response = await fetch('/upload-pdf?password={password}', {{
                    method: 'POST',
                    body: formData
                }});
                
                if (response.ok) {{
                    const data = await response.json();
                    status.innerHTML = `<div class="success">
                        ✅ <strong>${{data.filename}}</strong> enviado com sucesso!<br>
                        📏 ${{(data.size / 1024 / 1024).toFixed(2)}} MB<br>
                        📚 ${{data.indexed ? 'Arquivo será indexado' : 'Indexação pendente'}}
                    </div>`;
                }} else {{
                    const error = await response.text();
                    status.innerHTML = `<div class="error">❌ ${{error}}</div>`;
                }}
            }} catch (error) {{
                status.innerHTML = `<div class="error">❌ Erro de conexão</div>`;
            }}
        }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ==================== PÁGINA DE GERENCIAMENTO (ADMIN) ====================
@app.get("/admin")
async def admin_page(password: str = ""):
    """Página de gerenciamento de arquivos"""
    if password != "admin123":
        return RedirectResponse(url="/")
    
    files = get_uploaded_files()
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gerenciar - Licitabot</title>
        <style>
            body {{ 
                font-family: 'Arial', sans-serif;
                max-width: 800px; 
                margin: 0 auto; 
                padding: 20px;
                background: #f8fafc;
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
                border: none;
                cursor: pointer;
            }}
            .btn-download {{ 
                background: #0b3d5c; 
                color: white; 
            }}
            .btn-delete {{ 
                background: #dc2626; 
                color: white; 
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
            }}
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/chat?password={password}">💬 Chat</a>
            <a href="/upload?password={password}">📤 Upload</a>
            <a href="/admin?password={password}" style="background: #0b3d5c; color: white;">📁 Gerenciar</a>
        </div>
        
        <h2 style="color: #0b3d5c;">📁 Gerenciar Documentos</h2>
        <p>Total de arquivos: {len(files)}</p>
        
        <div class="file-list">
            {"".join([f'''
            <div class="file-item">
                <div>
                    <strong style="font-size: 16px;">📄 {f['name']}</strong><br>
                    <small style="color: #666;">📏 {f['size_mb']} MB • 🕒 {f['upload_date']}</small>
                </div>
                <div class="file-actions">
                    <a href="/download/{f['name']}?password={password}" class="btn btn-download">⬇️ Download</a>
                    <button class="btn btn-delete" onclick="deleteFile('{f['name']}')">🗑️ Excluir</button>
                </div>
            </div>
            ''' for f in files]) if files else '''
            <div class="empty">
                <div style="font-size: 48px; margin-bottom: 20px;">📭</div>
                <h3>Nenhum documento enviado</h3>
                <p>Vá para a página de upload para enviar seus primeiros documentos.</p>
                <a href="/upload?password=''' + password + '''" style="display: inline-block; margin-top: 20px; padding: 12px 24px; background: #0b3d5c; color: white; text-decoration: none; border-radius: 8px; font-weight: bold;">📤 Fazer Upload</a>
            </div>
            '''}
        </div>

        <script>
        async function deleteFile(filename) {{
            if (!confirm('Tem certeza que deseja excluir \"' + filename + '\"?')) {{
                return;
            }}
            
            try {{
                const response = await fetch('/delete-file/' + filename + '?password={password}', {{
                    method: 'DELETE'
                }});
                
                if (response.ok) {{
                    alert('✅ Arquivo excluído com sucesso!');
                    location.reload();
                }} else {{
                    alert('❌ Erro ao excluir arquivo');
                }}
            }} catch (error) {{
                alert('❌ Erro de conexão');
            }}
        }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ==================== API ENDPOINTS ====================
@app.post("/ask")
async def ask_question(request: Request, password: str = ""):
    """Endpoint de pesquisa"""
    if password not in ["admin123", "cliente123"]:
        raise HTTPException(status_code=401, detail="Não autorizado")
    
    try:
        payload = await request.json()
        question = payload.get("question", "").strip()
        
        if not question:
            return {"answer": "Por favor, digite uma pergunta."}
        
        # Usar sistema RAG
        hits = search(question, k=3)
        context = context_from_hits(hits)
        resposta = answer(question, context)
        
        return {"answer": resposta}
            
    except Exception as e:
        return {"answer": "❌ Erro interno. Tente novamente."}

@app.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...), password: str = ""):
    """Upload de PDF"""
    if password != "admin123":
        raise HTTPException(status_code=401, detail="Não autorizado")
    
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Apenas arquivos PDF são permitidos")
    
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    
    try:
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        # Indexar o arquivo
        if RAG_AVAILABLE:
            ingest_paths([file_path])
        
        return {
            "status": "success", 
            "filename": file.filename,
            "size": len(content),
            "indexed": RAG_AVAILABLE
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro: {str(e)}")

@app.get("/download/{filename}")
async def download_file(filename: str, password: str = ""):
    """Download de arquivo"""
    if password != "admin123":
        raise HTTPException(status_code=401, detail="Não autorizado")
    
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    
    return FileResponse(file_path, filename=filename)

@app.delete("/delete-file/{filename}")
async def delete_file(filename: str, password: str = ""):
    """Excluir arquivo"""
    if password != "admin123":
        raise HTTPException(status_code=401, detail="Não autorizado")
    
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    
    try:
        os.remove(file_path)
        return {"status": "success", "message": f"Arquivo {filename} excluído"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao excluir: {str(e)}")

# ==================== INICIALIZAÇÃO ====================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
