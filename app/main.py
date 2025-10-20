from fastapi import FastAPI, Request, UploadFile, File, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os, pathlib

app = FastAPI(title="Licitabot – Cloud")

# monta /static e /templates (pastas na raiz do repo)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

ACCESS_PASSWORD = (os.getenv("ACCESS_PASSWORD", "1234") or "1234").strip()

print("🔍 Diagnóstico: ACCESS_PASSWORD configurada como ->", repr(ACCESS_PASSWORD))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin123")

# ----- rotas -----

# login (página)
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

# login (verificação da senha)
@app.post("/login")
async def login(data: dict):
    password = (data or {}).get("password", "")
    if password != ACCESS_PASSWORD:
        return JSONResponse({"ok": False, "error": "Senha incorreta."}, status_code=401)
    return {"ok": True}

# página do chat
@app.get("/chat", response_class=HTMLResponse)
def chat(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# pergunta do chat (versão mínima; depois ativamos RAG)
@app.post("/ask")
async def ask(data: dict):
    q = (data or {}).get("question", "").strip()
    if not q:
        return {"answer": "Por favor, escreva sua pergunta."}
    return {"answer": f"Simulação de resposta sobre: {q}"}

# upload de PDF (admin)
@app.post("/upload_pdf")
async def upload_pdf(file: UploadFile = File(...), x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token de admin inválido.")
    pathlib.Path("/data/docs").mkdir(parents=True, exist_ok=True)
    dest = f"/data/docs/{file.filename}"
    with open(dest, "wb") as f:
        f.write(await file.read())
    return {"ok": True, "indexed": file.filename}

# saúde
@app.get("/health")
def health():
    return {"status": "ok"}
