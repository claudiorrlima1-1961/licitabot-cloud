import os
from openai import OpenAI

SYSTEM_PROMPT = (
    "Você é um assistente jurídico especializado em Licitações e Contratos no Brasil. "
    "Foque em: pregão eletrônico, gestão/fiscalização contratual, aditivos, reequilíbrio, sanções, rescisão e Lei 14.133/2021. "
    "Use o contexto somente se for pertinente e, quando usar, cite a fonte entre colchetes [arquivo - parte X]. "
    "Se houver variações estaduais/municipais, avise. "
    "Finalize com: 'Isto não substitui consulta/parecer jurídico formal.'"
)

def get_client() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY não configurada no Render.")
    return OpenAI(api_key=key)

def answer(question: str, context: str) -> str:
    client = get_client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Pergunta do usuário:\n{question}\n\nContexto (use se for útil e cite a fonte):\n{context}"}
    ]
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.1,
        max_tokens=900,
    )
    return res.choices[0].message.content
