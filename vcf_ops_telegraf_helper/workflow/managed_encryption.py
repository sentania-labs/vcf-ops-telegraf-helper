"""Read the Ops-managed agent's encrypted configuration format."""
import hashlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def decrypt_managed_config(key_pem: bytes, encrypted: bytes) -> bytes:
    """Decrypt a size-prefixed AES-CBC file and truncate it to the saved size."""
    if len(encrypted) < 40 or (len(encrypted) - 24) % 16:
        raise ValueError("encrypted file is truncated or has an invalid length")
    size = int.from_bytes(encrypted[:8], "little")
    if size > len(encrypted) - 24:
        raise ValueError("the saved size does not match the encrypted file")
    decryptor = Cipher(algorithms.AES(hashlib.sha256(key_pem).digest()), modes.CBC(encrypted[8:24])).decryptor()
    plaintext = (decryptor.update(encrypted[24:]) + decryptor.finalize())[:size]
    try:
        plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("decryption failed; the key or encrypted file may be wrong") from exc
    return plaintext
