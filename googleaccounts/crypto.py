from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TokenCryptoError(Exception):
    """Error that is safe to show to the user."""


def _fernet():
    key = getattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise TokenCryptoError("TOKEN_ENCRYPTION_KEY is not set (.env).")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise TokenCryptoError("TOKEN_ENCRYPTION_KEY has an invalid format.")


def encrypt_token(plain):
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_token(value):
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        raise TokenCryptoError("The saved token could not be decrypted (did the encryption key change?).")