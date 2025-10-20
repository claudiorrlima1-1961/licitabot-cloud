# LICITABOT CLOUD 2025 — VERSÃO ONLINE COM SENHA (PRONTO)

## Como publicar (Render.com)
1. Suba esta pasta inteira para um repositório no GitHub (ex.: licitabot-cloud-2025).
2. No Render.com, crie um Web Service usando Docker a partir do seu repositório.
3. Em Environment, adicione:
   - OPENAI_API_KEY = sua chave `sk-...`
   - ADMIN_TOKEN = senha de admin (para enviar PDFs)
   - ACCESS_PASSWORD = senha que você passará aos clientes
   - SECRET_KEY = texto longo (protege a sessão)
   - PORT = 10000
4. Em Disks, crie um disco de 1GB montado em `/data`.
5. Deploy → a URL pública abrirá a tela de login (senha = ACCESS_PASSWORD).

## Enviar/atualizar PDFs (Windows)
```
curl -X POST "https://SEU-NOME.onrender.com/upload_pdf" ^
  -H "X-Admin-Token: SUA_SENHA_DE_ADMIN" ^
  -F "file=@C:\caminho\para\SeuArquivo.pdf"
```

## Observações
- Os PDFs devem ter texto pesquisável (faça OCR se forem escaneados).
- As respostas citam as fontes no formato [arquivo - parte X].
- Aviso: “Isto não substitui consulta/parecer jurídico formal.”
