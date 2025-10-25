# -*- coding: utf-8 -*-
import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="Licitabot")

# Senhas de acesso
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
CLIENT_PASSWORD = os.getenv("CLIENT_PASSWORD", "cliente123")

# ==================== PÁGINA DE LOGIN ====================
@app.get("/")
async def login_page():
    html = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Login - Licitabot</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Arial', sans-serif;
                background: linear-gradient(135deg, #0b3d5c 0%, #0a3350 100%);
                height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            .login-container {
                background: white;
                padding: 40px;
                border-radius: 15px;
                box-shadow: 0 15px 35px rgba(0,0,0,0.2);
                width: 100%;
                max-width: 400px;
                text-align: center;
            }
            
            .logo {
                font-size: 50px;
                margin-bottom: 20px;
                color: #0b3d5c;
            }
            
            h1 {
                color: #0b3d5c;
                margin-bottom: 10px;
                font-size: 24px;
            }
            
            .subtitle {
                color: #666;
                margin-bottom: 30px;
                font-size: 14px;
            }
            
            .form-group {
                margin-bottom: 20px;
                text-align: left;
            }
            
            label {
                display: block;
                margin-bottom: 8px;
                color: #333;
                font-weight: bold;
                font-size: 14px;
            }
            
            input[type="text"],
            input[type="password"] {
                width: 100%;
                padding: 15px;
                border: 2px solid #ddd;
                border-radius: 8px;
                font-size: 16px;
                transition: border-color 0.3s;
            }
            
            input[type="text"]:focus,
            input[type="password"]:focus {
                border-color: #0b3d5c;
                outline: none;
            }
            
            .login-btn {
                width: 100%;
                padding: 15px;
                background: #0b3d5c;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: bold;
                cursor: pointer;
                transition: background 0.3s;
                margin-top: 10px;
            }
            
            .login-btn:hover {
                background: #0a3350;
            }
            
            .error-message {
                background: #fee2e2;
                color: #dc2626;
                padding: 12px;
                border-radius: 6px;
                margin-bottom: 20px;
                display: none;
            }
            
            .password-hint {
                margin-top: 20px;
                padding: 15px;
                background: #f8f9fa;
                border-radius: 6px;
                font-size: 12px;
                color: #666;
                text-align: left;
            }
            
            .user-options {
                display: flex;
                gap: 10px;
                margin-top: 15px;
            }
            
            .user-btn {
                flex: 1;
                padding: 10px;
                background: #f1f5f9;
                border: 2px solid #e1e5e9;
                border-radius: 6px;
                cursor: pointer;
                font-size: 12px;
                font-weight: bold;
            }
            
            .user-btn:hover {
                background: #0b3d5c;
                color: white;
            }
        </style>
    </head>
    <body>
        <div class="login-container">
            <div class="logo">🔐</div>
            <h1>Licitabot</h1>
            <p class="subtitle">Sistema de Gestão de Licitações</p>
            
            <div id="errorMessage" class="error-message"></div>
            
            <form id="loginForm">
                <div class="form-group">
                    <label for="username">USUÁRIO:</label>
                    <input type="text" id="username" name="username" required 
                           placeholder="Digite admin ou cliente">
                </div>
                
                <div class="form-group">
                    <label for="password">SENHA:</label>
                    <input type="password" id="password" name="password" required 
                           placeholder="Digite a senha de acesso">
                </div>
                
                <button type="submit" class="login-btn">🔓 ENTRAR NO SISTEMA</button>
            </form>

            <div class="user-options">
                <button class="user-btn" onclick="fillAdmin()">👑 Admin</button>
                <button class="user-btn" onclick="fillClient()">👤 Cliente</button>
            </div>
            
            <div class="password-hint">
                <strong>Credenciais de Teste:</strong><br>
                • Admin: usuario <strong>admin</strong> | senha <strong>admin123</strong><br>
                • Cliente: usuario <strong>cliente</strong> | senha <strong>cliente123</strong>
            </div>
        </div>

        <script>
            function fillAdmin() {
                document.getElementById('username').value = 'admin';
                document.getElementById('password').value = 'admin123';
            }
            
            function fillClient() {
                document.getElementById('username').value = 'cliente';
                document.getElementById('password').value = 'cliente123';
            }

            document.getElementById('loginForm').addEventListener('submit', async function(e) {
                e.preventDefault();
                
                const username = document.getElementById('username').value;
                const password = document.getElementById('password').value;
                const errorDiv = document.getElementById('errorMessage');
                const button = document.querySelector('.login-btn');
                
                // Mostrar loading
                button.innerHTML = '⏳ AUTENTICANDO...';
                button.disabled = true;
                
                try {
                    const response = await fetch('/auth/login', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({
                            username: username,
                            password: password
                        })
                    });
                    
                    if (response.ok) {
                        button.innerHTML = '✅ ACESSO CONCEDIDO!';
                        setTimeout(() => {
                            window.location.href = '/dashboard';
                        }, 1000);
                    } else {
                        const error = await response.json();
                        errorDiv.textContent = '❌ ' + error.detail;
                        errorDiv.style.display = 'block';
                        button.innerHTML = '🔓 ENTRAR NO SISTEMA';
                        button.disabled = false;
                    }
                } catch (error) {
                    errorDiv.textContent = '❌ Erro de conexão. Tente novamente.';
                    errorDiv.style.display = 'block';
                    button.innerHTML = '🔓 ENTRAR NO SISTEMA';
                    button.disabled = false;
                }
            });
            
            // Focar no campo de usuário
            document.getElementById('username').focus();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ==================== AUTENTICAÇÃO ====================
@app.post("/auth/login")
async def login(request: Request):
    """Processa o login"""
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        
        # Verifica credenciais
        if username == "admin" and password == ADMIN_PASSWORD:
            user_data = {"username": "admin", "role": "admin", "name": "Administrador"}
        elif username == "cliente" and password == CLIENT_PASSWORD:
            user_data = {"username": "cliente", "role": "user", "name": "Cliente"}
        else:
            raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")
        
        return JSONResponse({
            "status": "success", 
            "user": user_data
        })
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Erro interno do sistema")

# ==================== DASHBOARD ====================
@app.get("/dashboard")
async def dashboard():
    """Página principal após login"""
    html = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Dashboard - Licitabot</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: 'Arial', sans-serif;
                background: #f5f7fa;
                color: #333;
            }
            .navbar {
                background: #0b3d5c;
                color: white;
                padding: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .nav-links { display: flex; gap: 15px; }
            .nav-links a {
                color: white;
                text-decoration: none;
                padding: 10px 15px;
                border-radius: 5px;
                transition: background 0.3s;
            }
            .nav-links a:hover { background: rgba(255,255,255,0.1); }
            .container { max-width: 800px; margin: 0 auto; padding: 40px 20px; }
            .welcome { 
                background: white;
                padding: 40px;
                border-radius: 10px;
                box-shadow: 0 5px 15px rgba(0,0,0,0.1);
                text-align: center;
                margin-bottom: 30px;
            }
            .features {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
            }
            .feature-card {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 5px 15px rgba(0,0,0,0.1);
                text-align: center;
                transition: transform 0.3s;
            }
            .feature-card:hover { transform: translateY(-5px); }
            .feature-icon { font-size: 40px; margin-bottom: 15px; }
            .btn {
                display: inline-block;
                margin-top: 15px;
                padding: 12px 25px;
                background: #0b3d5c;
                color: white;
                text-decoration: none;
                border-radius: 6px;
                font-weight: bold;
            }
            .btn:hover { background: #0a3350; }
        </style>
    </head>
    <body>
        <div class="navbar">
            <div style="font-size: 20px; font-weight: bold;">💼 Licitabot</div>
            <div class="nav-links">
                <a href="/dashboard">🏠 Dashboard</a>
                <a href="/chat">💬 Chat</a>
                <a href="/upload">📤 Upload</a>
                <a href="/logout">🚪 Sair</a>
            </div>
        </div>
        
        <div class="container">
            <div class="welcome">
                <h1>Bem-vindo ao Licitabot! 🎉</h1>
                <p style="color: #666; margin-top: 10px;">
                    Sistema profissional de gestão e consulta de documentos de licitação
                </p>
            </div>
            
            <div class="features">
                <div class="feature-card">
                    <div class="feature-icon">💬</div>
                    <h3>Chat Inteligente</h3>
                    <p>Faça perguntas sobre seus documentos usando IA</p>
                    <a href="/chat" class="btn">Acessar Chat</a>
                </div>
                
                <div class="feature-card">
                    <div class="feature-icon">📁</div>
                    <h3>Gerenciar PDFs</h3>
                    <p>Envie e visualize documentos</p>
                    <a href="/upload" class="btn">Upload de PDFs</a>
                </div>
                
                <div class="feature-card">
                    <div class="feature-icon">🔍</div>
                    <h3>Pesquisa Avançada</h3>
                    <p>Encontre informações específicas</p>
                    <a href="/chat" class="btn">Fazer Pesquisa</a>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ==================== PÁGINAS BÁSICAS ====================
@app.get("/chat")
async def chat():
    html = """
    <html>
    <head><title>Chat</title></head>
    <body style="font-family: Arial; padding: 20px;">
        <h1>💬 Chat - Em Desenvolvimento</h1>
        <p>Esta funcionalidade estará disponível em breve!</p>
        <a href="/dashboard">← Voltar</a>
    </body>
    </html>
    """
    return HTMLResponse(html)

@app.get("/upload")
async def upload():
    html = """
    <html>
    <head><title>Upload</title></head>
    <body style="font-family: Arial; padding: 20px;">
        <h1>📤 Upload - Em Desenvolvimento</h1>
        <p>Esta funcionalidade estará disponível em breve!</p>
        <a href="/dashboard">← Voltar</a>
    </body>
    </html>
    """
    return HTMLResponse(html)

@app.get("/logout")
async def logout():
    return RedirectResponse(url="/")

# ==================== HEALTH CHECK ====================
@app.get("/health")
async def health():
    return {"status": "healthy", "service": "Licitabot"}

# ==================== INICIALIZAÇÃO ====================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
