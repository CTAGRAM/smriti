"""On-device query understanding. Turns plain language into Qdrant payload filters
without any network — household names resolve through the local register."""

import re
import time

KIND_WORDS = {
    "protocol": r"\b(protocols?|guidelines?|advisor(?:y|ies)|guidance)\b",
    "note": r"\b(notes?|visits?|observations?)\b",
    "household": r"\b(households?|records?|families|family)\b",
}
WINDOWS = [
    (r"\btoday\b", 1, "today"),
    (r"\byesterday\b", 2, "since yesterday"),
    (r"\b(this|past|last) week\b|\blast 7 days\b", 7, "last 7 days"),
    (r"\b(this|past|last) month\b|\blast 30 days\b", 30, "last 30 days"),
]


def parse(q: str, register: dict[str, str]) -> tuple[str, dict, list[str]]:
    filters, chips, text = {}, [], q
    hh = re.search(r"\bHH-\d{3}\b", q, re.I)
    if hh:
        filters["household"] = hh.group(0).upper()
    else:
        for name, h in register.items():
            if re.search(rf"\b{re.escape(name)}('s)?\b", q, re.I):
                filters["household"] = h
                break
    if "household" in filters:
        chips.append(f"household = {filters['household']}")
    if re.search(r"\burgent\b", q, re.I):
        filters["priority"] = "urgent"
        chips.append("priority = urgent")
    for kind, pattern in KIND_WORDS.items():
        if re.search(pattern, q, re.I):
            filters["kind"] = kind
            chips.append(f"kind = {kind}")
            break
    for pattern, days, label in WINDOWS:
        if re.search(pattern, q, re.I):
            filters["since"] = time.time() - days * 86400
            chips.append(f"timestamp ≥ {label}")
            text = re.sub(pattern, " ", text, flags=re.I)
            break
    return re.sub(r"\s+", " ", text).strip() or q, filters, chips
