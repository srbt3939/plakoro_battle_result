"""AES-256-GCM token encryption for Discord OAuth2 access tokens in Sessions (手順7 §2-1)."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def derive_aes256_key(secret_material: str | bytes) -> bytes:
    """Normalize a 32-byte key (or arbitrary secret string) into 32 bytes for AES-256-GCM."""
    if isinstance(secret_material, bytes):
        if len(secret_material) == 32:
            return secret_material
        return hashlib.sha256(secret_material).digest()
    raw = secret_material.strip()
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if len(decoded) == 32:
            return decoded
    except Exception:
        pass
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_access_token(access_token: str, key: bytes) -> str:
    aesgcm = AESGCM(derive_aes256_key(key))
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, access_token.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_access_token(encoded_blob: str, key: bytes) -> str:
    raw = base64.urlsafe_b64decode(encoded_blob.encode("ascii"))
    if len(raw) < 28:
        raise ValueError("Invalid encrypted token payload.")
    nonce, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(derive_aes256_key(key))
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")
