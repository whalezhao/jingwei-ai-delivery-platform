"""模型密钥落库必须是密文；历史明文值仍要能读。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.crypto import encrypt_secret
from app.modelconfig import model_api_key


class _Cfg:
    def __init__(self, key):
        self.api_key = key


def test_model_key_is_encrypted_at_rest_and_decrypts():
    cfg = _Cfg(encrypt_secret("sk-test-123"))
    assert cfg.api_key.startswith("enc:v1:")
    assert model_api_key(cfg) == "sk-test-123"


def test_model_key_keeps_legacy_plaintext_readable():
    assert model_api_key(_Cfg("sk-legacy")) == "sk-legacy"
    assert model_api_key(_Cfg(None)) == ""
    assert model_api_key(None) == ""