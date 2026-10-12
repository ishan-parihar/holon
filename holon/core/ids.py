"""ULID-style identifiers: 48-bit ms timestamp + 80 random bits, Crockford base32.

Time-sortable and generated offline on any host, so ids never collide across
machines and sort by creation order. Monotonic within one process.
"""

from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_lock = threading.Lock()
_last_ms = -1
_last_rand = 0

PREFIXES = {"task": "tsk", "plan": "pln", "run": "run", "item": "itm", "episode": "eps",
            "message": "msg", "employee": "emp", "dept": "dept", "schedule": "sch"}


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def ulid(now_ms: int | None = None) -> str:
    global _last_ms, _last_rand
    with _lock:
        ms = int(time.time() * 1000) if now_ms is None else now_ms
        if ms == _last_ms:
            _last_rand = (_last_rand + 1) & ((1 << 80) - 1)
        else:
            _last_ms = ms
            _last_rand = int.from_bytes(os.urandom(10), "big")
        return _encode(ms, 10) + _encode(_last_rand, 16)


def new_id(kind: str) -> str:
    return f"{PREFIXES[kind]}_{ulid()}"
