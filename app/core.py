import os
from openai import OpenAI

SYSTEM_PROMPT = (
    "Você é um assistente jurídico especializado em Licitações e Contratos no Brasil.\n"
    "REGRAS OBRIGATÓRIAS:\n"
    "1) Responda APENAS com base no CONTEXTO fornecido.\n"
    "2) Se o CONTEXTO não contiver a resposta, diga literalmente: "
    "'Não encontrei essa informação na base de documentos.'\n"
    "3) Quando usar trechos do CONTEXTO, cite a fonte entre colchetes [arquivo - parte X].\n"
    "4) Indique variações estaduais/municipais quando relevante.\n"
    "5) Termine com: 'Isto não substitui consulta/parecer jurídico formal.'"
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
        # Instrução reforço: contexto é a ÚNICA fonte
        {"role": "system", "content": "IMPORTANTE: ignore qualquer conhecimento externo; use somente o CONTEXTO."},
        {"role": "user", "content": f"Pergunta: {question}\n\nCONTEXTO (use apenas este material):\n{context}"}
    ]
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.1,
        max_tokens=900
    )
    return res.choices[0].message.content
