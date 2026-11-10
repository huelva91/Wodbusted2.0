import os
from cryptography.fernet import Fernet
from config import SECRET_KEY_FILE

_cipher = None

def _get_or_create_key() -> bytes:
    env_key = os.getenv("ENCRYPTION_KEY")
    if env_key:
        return env_key.encode()

    if SECRET_KEY_FILE.exists():
        with open(SECRET_KEY_FILE, "rb") as f:
            return f.read().strip()
    else:
        key = Fernet.generate_key()
        with open(SECRET_KEY_FILE, "wb") as f:
            f.write(key)
        # Restrict permissions for security
        try:
            os.chmod(SECRET_KEY_FILE, 0o600)
        except Exception:
            pass
        return key

def get_cipher() -> Fernet:
    global _cipher
    if _cipher is None:
        key = _get_or_create_key()
        _cipher = Fernet(key)
    return _cipher

def encrypt_password(plaintext: str) -> str:
    """Encrypt a password string into a base64 fernet token string."""
    if not plaintext:
        return ""
    cipher = get_cipher()
    return cipher.encrypt(plaintext.encode()).decode()

def decrypt_password(ciphertext: str) -> str:
    """Decrypt a fernet token string back into plaintext password."""
    if not ciphertext:
        return ""
    try:
        cipher = get_cipher()
        return cipher.decrypt(ciphertext.encode()).decode()
    except Exception:
        return ""
