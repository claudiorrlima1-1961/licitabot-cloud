# Etapa base
FROM python:3.11-slim

# Evita prompts interativos
ENV DEBIAN_FRONTEND=noninteractive

# Instala utilitários e OCR (poppler + tesseract)
RUN apt-get update && apt-get install -y \
    build-essential \
    poppler-utils \
    tesseract-ocr \
    libtesseract-dev \
    libpoppler-cpp-dev \
    libgl1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Cria diretório do app
WORKDIR /app

# Copia arquivos do projeto
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cria diretórios persistentes esperados
RUN mkdir -p /data/uploaded_pdfs /data/chroma

# Expõe porta padrão
EXPOSE 10000

# Comando de inicialização
CMD ["python", "-m", "app.main"]
