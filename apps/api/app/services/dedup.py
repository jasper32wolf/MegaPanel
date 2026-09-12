from __future__ import annotations

import hashlib
import re
from collections import Counter


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^\w\sа-яё]+", " ", text, flags=re.I)
    return [t for t in text.split() if len(t) > 2]


def simhash64(text: str) -> str:
    """64-bit SimHash for near-duplicate detection (TZ 3.3)."""
    tokens = _tokenize(text)
    if not tokens:
        return "0" * 16
    weights = Counter(tokens)
    v = [0] * 64
    for token, w in weights.items():
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        for i in range(64):
            bit = (h >> i) & 1
            v[i] += w if bit else -w
    out = 0
    for i, val in enumerate(v):
        if val > 0:
            out |= 1 << i
    return f"{out:016x}"


def hamming(a: str, b: str) -> int:
    x = int(a, 16) ^ int(b, 16)
    return x.bit_count()


def similarity_from_hamming(distance: int, bits: int = 64) -> float:
    return 1.0 - (distance / bits)


def compare_texts(a: str, b: str) -> float:
    return similarity_from_hamming(hamming(simhash64(a), simhash64(b)))


def minhash_buckets(text: str, num_hashes: int = 32) -> list[int]:
    """Simplified MinHash signature for LSH bucketing."""
    tokens = set(_tokenize(text))
    if not tokens:
        return [0] * num_hashes
    sig: list[int] = []
    for i in range(num_hashes):
        best = min(int(hashlib.sha1(f"{i}:{t}".encode()).hexdigest(), 16) for t in tokens)
        sig.append(best % (2**31))
    return sig
