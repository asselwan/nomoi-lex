"""SHA-256 hashing for claim IDs — no PHI in the UI."""

import hashlib


def hash_claim_id(claim_id: str) -> str:
    """Return first 12 hex chars of SHA-256 of the claim ID."""
    return hashlib.sha256(claim_id.encode()).hexdigest()[:12]


def hash_file(content: bytes) -> str:
    """Return full SHA-256 hex digest of file content for dedup."""
    return hashlib.sha256(content).hexdigest()
