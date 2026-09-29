"""System One triage on the device, through OpenJev's Jev-compatible API.

A small encoder model (Laya, 421M, CPU) answers typed questions about each note
with probabilities: no text is generated, so an answer cannot go off-schema.
Smriti uses these for triage only — privacy decisions stay deterministic."""

import json
import os
import time
import urllib.request

URL = os.environ.get("OPENJEV_URL", "http://127.0.0.1:8093/v1/systemone")
SEVERITY = ["routine", "watch", "refer soon", "emergency"]
QUESTIONS = {
    "danger": {"type": "noul", "instructions": "Does this health worker's note describe a danger sign that needs referral to a health facility today?"},
    "severity": {"type": "score", "instructions": "How serious is the situation described?", "criteria": SEVERITY},
    "follow_up": {"type": "noul", "instructions": "Does this household need another home visit within two days?"},
}


def triage(text: str, timeout: float = 4.0) -> dict | None:
    """Returns typed answers, or None when no local OpenJev server is running."""
    body = json.dumps({"model": "jev-latest", "state": text, "questions": QUESTIONS}).encode()
    t = time.perf_counter()
    try:
        req = urllib.request.Request(URL, body, {"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except OSError:
        return None
    a = data["answers"]
    severity = float(a["severity"]["score"])
    return {
        "danger": round(float(a["danger"]["noul"]), 3),
        "severity": round(severity, 2),
        "severity_label": SEVERITY[min(3, round(severity))],
        "follow_up": round(float(a["follow_up"]["noul"]), 3),
        "model": data.get("model", "openjev"),
        "ms": round((time.perf_counter() - t) * 1000),
    }
