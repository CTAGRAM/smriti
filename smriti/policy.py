"""The on-device sync policy: decides, offline and before anything leaves the phone,
whether a memory stays local, syncs redacted, and how urgently."""

import re
from dataclasses import dataclass, field

import numpy as np

from .embed import dense_many

PHONE = re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)")
AADHAAR = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")
PRIVATE = re.compile(r"\b(private|personal|do not share|don't share)\b", re.I)

# Short statements of danger signs; a note close to any of them in meaning is urgent.
DANGER_SIGNS = [
    "pregnant woman with severe headache and blurred vision",
    "bleeding during pregnancy",
    "convulsions or fits",
    "high fever that will not come down",
    "newborn not feeding and very weak",
    "baby breathing very fast with chest indrawing",
    "child unconscious or very lethargic after diarrhoea",
    "severe abdominal pain in pregnancy",
    "reduced baby movements in the womb",
]
URGENT_SIMILARITY = 0.72

_danger: np.ndarray | None = None


def _danger_matrix() -> np.ndarray:
    global _danger
    if _danger is None:
        _danger = np.array(dense_many(DANGER_SIGNS))
    return _danger


@dataclass
class Decision:
    share: str  # "local_only" | "redacted" | "full"
    priority: str  # "urgent" | "normal"
    redacted_text: str
    reasons: list[str] = field(default_factory=list)
    danger_match: str | None = None
    danger_score: float = 0.0


def redact(text: str, register: dict[str, str]) -> tuple[str, list[str]]:
    """Replaces registered names with household IDs, and masks phone/Aadhaar numbers."""
    found = []
    out = text
    for name, hh in sorted(register.items(), key=lambda kv: -len(kv[0])):
        pattern = re.compile(rf"\b{re.escape(name)}\b", re.I)
        if pattern.search(out):
            out = pattern.sub(hh, out)
            found.append(f"name → {hh}")
    if PHONE.search(out):
        out = PHONE.sub("[phone]", out)
        found.append("phone number masked")
    if AADHAAR.search(out):
        out = AADHAAR.sub("[aadhaar]", out)
        found.append("Aadhaar number masked")
    return out, found


def decide(text: str, dense: list[float], register: dict[str, str], private: bool = False) -> Decision:
    redacted, pii = redact(text, register)
    sims = _danger_matrix() @ np.array(dense)
    best = int(np.argmax(sims))
    score = float(sims[best])
    urgent = score >= URGENT_SIMILARITY

    if private or PRIVATE.search(text):
        return Decision("local_only", "normal", redacted, ["marked private — never leaves the device"])

    reasons = [f"personal data redacted before sync ({', '.join(pii)})"] if pii else ["no personal data found"]
    if urgent:
        reasons.append(f"danger sign — “{DANGER_SIGNS[best]}” ({score:.2f}) — syncs first")
    return Decision("redacted" if pii else "full", "urgent" if urgent else "normal", redacted, reasons,
                    DANGER_SIGNS[best] if urgent else None, score)
