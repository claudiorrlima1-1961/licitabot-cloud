from .rag_store import search, context_from_hits
from .core import answer
from fastapi import Depends, Header, HTTPException

@app.post("/ask")
async def ask(payload: dict, ok: bool = Depends(require_auth), x_admin_token: str = Header(None)):
    q = (payload or {}).get("question", "").strip()
    if not q:
        return {"answer": "Por favor, escreva sua pergunta."}

    # 1) Busca nos PDFs
    hits = search(q, k=4)

    # 2) Se NÃO houver contexto, não chama a IA
    if not hits:
        return {"answer": "Não encontrei essa informação na base de documentos."}

    # 3) Monta contexto apenas com os trechos encontrados
    ctx = context_from_hits(hits)

    # 4) Chama a OpenAI com prompt estrito (apenas contexto)
    ans = answer(q, ctx)

    # (Opcional) Retornar fontes e trechos se você enviar X-Admin-Token
    if x_admin_token == ADMIN_TOKEN:
        return {
            "answer": ans,
            "citations": [
                {"source": md.get("source"), "chunk": md.get("chunk"), "excerpt": doc[:280]}
                for (doc, md) in hits
            ]
        }

    return {"answer": ans}
