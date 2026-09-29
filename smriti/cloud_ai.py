"""The cloud half of the AI workflow. Retrieval always happens on the device; when a
connection exists, a cloud model can turn those results into an answer — but it
only ever receives redacted text."""

import os

from google import genai
from google.genai import types

from . import policy

MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")


def answer(question: str, hits: list[dict], register: dict[str, str]) -> dict:
    context = []
    for i, h in enumerate(hits[:6], 1):
        text, _ = policy.redact(h["text"] or "", register)
        context.append({"n": i, "id": h["id"], "kind": h["kind"], "text": text})
    q_redacted, _ = policy.redact(question, register)
    prompt = (
        "You support a community health worker in rural India. Answer ONLY from the numbered memory "
        "snippets below, citing them like [1]. If they are not enough, say so. Use short, practical sentences. "
        "Household IDs like HH-014 stand in for names. This is decision support, not a diagnosis: when a danger "
        "sign is present, the advice is to refer to the nearest health facility.\n\n"
        + "\n".join(f"[{c['n']}] ({c['kind']}) {c['text']}" for c in context)
        + f"\n\nQuestion: {q_redacted}"
    )
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    res = client.models.generate_content(model=MODEL, contents=prompt, config=types.GenerateContentConfig(temperature=0.2))
    return {"answer": res.text, "sent_to_cloud": [q_redacted] + [c["text"] for c in context], "model": MODEL}
