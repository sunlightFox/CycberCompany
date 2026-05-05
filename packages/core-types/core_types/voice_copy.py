from __future__ import annotations

import hashlib
from typing import Iterable


def pick_variant(seed: str, variants: Iterable[str], *, default: str = "") -> str:
    items = [str(item).strip() for item in variants if str(item).strip()]
    if not items:
        return default
    if len(items) == 1:
        return items[0]
    digest = hashlib.sha1(seed.encode("utf-8")).digest()
    return items[digest[0] % len(items)]
