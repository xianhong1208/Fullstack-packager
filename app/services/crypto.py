"""Symmetric encryption for secrets stored at rest (Git credentials).

Tokens entered in the console must be usable later to authenticate `git` (unlike
passwords, which are only ever compared, so a one-way hash suffices). They are
therefore encrypted with Fernet (AES-128-CBC + HMAC) under a key that never
leaves the server. The key is loaded from SETTINGS_ENCRYPTION_KEY, or generated
once and appended to .env, mirroring how the JWT secret is handled.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class DecryptionError(RuntimeError):
    """Raised when a stored ciphertext cannot be decrypted with the current key."""


def _load_or_create_key() -> str:
    # NOTE: auto-generation assumes a single writer. With multiple worker
    # processes and no SETTINGS_ENCRYPTION_KEY, each could generate a different
    # key. The service runs single-process by design (see deploy/build-center.service);
    # for any other setup, set SETTINGS_ENCRYPTION_KEY explicitly before starting.
    key = get_settings().settings_encryption_key
    if key:
        return key

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    new_key = Fernet.generate_key().decode()
    try:
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(
                "\n# Auto-generated key for encrypting stored secrets (do not remove)\n"
                f"SETTINGS_ENCRYPTION_KEY={new_key}\n"
            )
        os.chmod(env_path, 0o600)
    except OSError:
        pass  # Fallback: key lives only in memory for this process
    return new_key


@lru_cache
def _fernet() -> Fernet:
    return Fernet(_load_or_create_key().encode())


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret; returns URL-safe base64 ciphertext (str)."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a secret produced by encrypt_secret()."""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError(
            "Stored secret could not be decrypted — the SETTINGS_ENCRYPTION_KEY "
            "does not match the one used to encrypt it."
        ) from exc
