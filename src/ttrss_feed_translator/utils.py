from __future__ import annotations

import hashlib


def compute_source_hash(title: str, content: str) -> str:
    payload = title.encode("utf-8") + b"\0" + content.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

