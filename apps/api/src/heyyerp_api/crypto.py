"""Column-level encryption for sensitive values such as vendor bank account numbers.

AES-256-GCM with a random nonce per value. The key comes from DATA_ENCRYPTION_KEY (32 random
bytes, base64). Each value is also bound to the record it belongs to (the "context", for example
the vendor id), so a ciphertext copied onto another row cannot be read. Stored form:
`v1:` + base64(nonce + ciphertext). The prefix is the key version, so a new key can be introduced
later and old values still read until they are re-encrypted. Without a key the features that need
it answer 503 instead of storing the value in the clear. Generate a key with:

    python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, status

from heyyerp_api.config import get_settings

VERSION = "v1"


def _cipher() -> AESGCM:
    key = get_settings().data_encryption_key
    if not key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Data encryption is not configured (DATA_ENCRYPTION_KEY)")
    try:
        raw = base64.b64decode(key, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "DATA_ENCRYPTION_KEY is not valid base64") from exc
    if len(raw) != 32:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "DATA_ENCRYPTION_KEY must be 32 bytes")
    return AESGCM(raw)


def encrypt(value: str, context: str) -> str:
    nonce = os.urandom(12)
    sealed = _cipher().encrypt(nonce, value.encode("utf-8"), context.encode("utf-8"))
    return f"{VERSION}:{base64.b64encode(nonce + sealed).decode('ascii')}"


def decrypt(stored: str, context: str) -> str:
    version, _, body = stored.partition(":")
    if version != VERSION:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "The stored value uses an unknown encryption version")
    raw = base64.b64decode(body)
    try:
        return _cipher().decrypt(raw[:12], raw[12:], context.encode("utf-8")).decode("utf-8")
    except InvalidTag as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "The stored value cannot be decrypted with this key") from exc
