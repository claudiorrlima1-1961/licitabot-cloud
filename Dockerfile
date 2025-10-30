# Usa uma imagem leve e moderna do Python
FROM python:3.11-slim

# Define o diretório de trabalho
WORKDIR /app

# Instala dependências de sistema para OCR e PDF
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-por \
        tesseract-ocr-eng \
        libgl1 \
        ghostscript \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Copia os arquivos do projeto
COPY . .

# Instala as dependências do Python
RUN pip install --upgrade pip && pip install -r requirements.txt

# Expõe a porta (Render usa $PORT automaticamente)
EXPOSE 10000

# Comando de inicialização
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
