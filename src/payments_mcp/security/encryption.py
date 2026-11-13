"""Authenticated encryption for biller-specific customer parameters."""

from cryptography.fernet import Fernet, InvalidToken


class AccountCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise ValueError("Registered account data could not be decrypted") from exc
