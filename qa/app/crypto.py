"""环境密钥的对称加密。

密钥从 `ENVIRONMENT_SECRET_KEY` 派生。生产必须显式设置，否则开发默认值一变
旧密文就解不开。
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

PREFIX = "enc:v1:"


@lru_cache
def _fernet() -> Fernet:
    material = get_settings().environment_secret_key
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return PREFIX + _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    """兼容没有前缀的历史明文值；密文解不开时给出可操作的错误。"""
    if not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError(
            "环境密钥无法解密：ENVIRONMENT_SECRET_KEY 可能已更换"
        ) from exc


def is_encrypted(value: str | None) -> bool:
    return bool(value and value.startswith(PREFIX))


def _self_check() -> None:
    sample = "aster-qa-secret"
    token = encrypt_secret(sample)
    assert token.startswith(PREFIX)
    assert decrypt_secret(token) == sample
    assert decrypt_secret("plain") == "plain"
    print("crypto ok")


if __name__ == "__main__":
    _self_check()
