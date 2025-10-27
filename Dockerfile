# Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 1) Instalar dependências
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 2) Copiar o código
COPY app ./app

# 3) IMPORTANTÍSSIMO: copiar assets para os caminhos que o FastAPI espera
#    Seu main.py usa:
#      Jinja2Templates(directory="templates")
#      StaticFiles(directory="static")
#    Então vamos copiar de app/... para /templates e /static:
COPY app/templates ./templates
COPY app/static ./static

# 4) Start
ENV PORT=10000
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
