"""SHA-256 hashing for captured crawl content — the basis for
SourceSnapshot.content_hash and for detecting "nothing changed since the
last crawl" without re-diffing full page content."""

import hashlib


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
