"""敏感字段（GitLab token / LLM apikey）落库前 Fernet 加密。

密钥从 settings.secret_key 派生，不单独管理第二把钥匙。
空串/None 原样存（表示未配置），避免解密空值报错。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


def _fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(secret: str, value: str | None) -> str | None:
    if not value:
        return value
    return _fernet(secret).encrypt(value.encode()).decode()


def decrypt(secret: str, value: str | None) -> str | None:
    if not value:
        return value
    return _fernet(secret).decrypt(value.encode()).decode()
