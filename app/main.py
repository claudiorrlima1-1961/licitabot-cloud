from fastapi import FastAPI, Request, UploadFile, File, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os, pathlib

app = FastAPI(title="Licitabot – Cloud")

# monta /static e /templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "1234")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin123")

@app.get("/chat", response_class=HTMLResponse)
def chat(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/login")
async def login(data: dict):
    if data.get("password") != ACCESS_PASSWORD:
        return JSONResponse({"ok": False, "error": "Senha incorreta."}, status_code=401)
    return {"ok": True}

@app.post("/ask")
async def ask(data: dict):
    q = (data or {}).get("question", "")
    return {"answer": f"Simulação de resposta sobre: {q}"}

@app.post("/upload_pdf")
async def upload_pdf(file: UploadFile = File(...), x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token de admin inválido.")
    pathlib.Path("/data/docs").mkdir(parents=True, exist_ok=True)
    path = f"/data/docs/{file.filename}"
    with open(path, "wb") as f:
        f.write(await file.read())
    return {"ok": True, "indexed": file.filename}
