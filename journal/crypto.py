"""Encryption and decryption using Fernet with PBKDF2 key derivation."""

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Random salt is generated per file for key derivation
SALT_SIZE = 16
ITERATIONS = 480000  # OWASP recommendation for PBKDF2-SHA256


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key from a passphrase using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
    return key


def encrypt(plaintext: str, passphrase: str) -> bytes:
    """Encrypt plaintext with a passphrase.

    Returns salt (16 bytes) + ciphertext.
    """
    salt = os.urandom(SALT_SIZE)
    key = derive_key(passphrase, salt)
    fernet = Fernet(key)
    ciphertext = fernet.encrypt(plaintext.encode())
    return salt + ciphertext


def decrypt(data: bytes, passphrase: str) -> str:
    """Decrypt data with a passphrase.

    Expects salt (16 bytes) + ciphertext format.
    Raises InvalidToken if passphrase is wrong.
    """
    salt = data[:SALT_SIZE]
    ciphertext = data[SALT_SIZE:]
    key = derive_key(passphrase, salt)
    fernet = Fernet(key)
    plaintext = fernet.decrypt(ciphertext)
    return plaintext.decode()


__all__ = ["encrypt", "decrypt", "InvalidToken"]
