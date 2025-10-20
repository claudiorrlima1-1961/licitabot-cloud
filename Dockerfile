FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY static ./static

VOLUME ["/data"]

ENV PORT=10000
EXPOSE 10000

CMD python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
