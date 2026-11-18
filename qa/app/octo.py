"""octo bot 发群消息的最小实现。

qa 与 Vega 是两个独立镜像，不能 import `api/vega`，这里只搬「发文本 + @」需要的
部分；wire format 与 `api/vega/octo.py` 保持一致（那边是实测过坑的真源，改协议时
两边要一起改）。token 由调用方从库里解好传进来，本模块不碰数据库。
"""

from __future__ import annotations

import json
import logging
import urllib.request
import uuid

from .config import get_settings

log = logging.getLogger(__name__)

_TIMEOUT = 20


def _api_url() -> str:
    return (get_settings().octo_api_url or "").rstrip("/")


def _token(token: str | None) -> str:
    return token or get_settings().octo_bot_token


def _get(path: str, token: str | None) -> dict | list:
    req = urllib.request.Request(
        f"{_api_url()}{path}", headers={"Authorization": f"Bearer {_token(token)}"}
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode())


def _post(path: str, payload: dict, token: str | None) -> None:
    req = urllib.request.Request(
        f"{_api_url()}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {_token(token)}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT):
        pass


def group_members(chat_id: str, token: str | None) -> dict[str, str]:
    """群成员 uid → 昵称；拉不到就返回空（宁可不 @，也不发 uid 天书）。

    bot 是 AI 不是人：robot 标志为准，uid 带 _bot 后缀兜底。
    """
    try:
        data = _get(f"/v1/bot/groups/{chat_id}/members", token)
    except Exception:
        log.warning("octo 群成员拉取失败 chat=%s", chat_id, exc_info=True)
        return {}
    members = data.get("members", data) if isinstance(data, dict) else data
    out: dict[str, str] = {}
    for m in members or []:
        if not (isinstance(m, dict) and m.get("uid") and m.get("name")):
            continue
        if m.get("robot") or str(m["uid"]).endswith("_bot"):
            continue
        out[str(m["uid"])] = str(m["name"])
    return out


def _utf16len(s: str) -> int:
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in s)


def build_mentions(
    body: str, uid_to_name: dict[str, str], mention_uids: list[str]
) -> tuple[str, list[dict]]:
    """把要 @ 的 uid 变成 (正文, entities)。昵称查不到的宁可不 @。"""
    matched = [(u, uid_to_name[u]) for u in mention_uids if u in uid_to_name]
    missing = [(u, nm) for u, nm in matched if f"@{nm}" not in body]
    text = (" ".join(f"@{nm}" for _, nm in missing) + " " + body) if missing else body

    entities = []
    pos = 0
    for uid, name in matched:
        mention = f"@{name}"
        idx = text.find(mention, pos)
        if idx < 0:
            continue
        entities.append(
            {"uid": uid, "offset": _utf16len(text[:idx]), "length": _utf16len(mention)}
        )
        pos = idx + len(mention)
    return text, entities


def send_text(
    chat_id: str,
    body: str,
    mention_uids: list[str] | None = None,
    token: str | None = None,
) -> bool:
    """发文本 + @。未配置 octo 时静默返回 False，调用方负责留痕。"""
    if not (chat_id and _api_url() and _token(token)):
        return False
    try:
        members = group_members(chat_id, token) if mention_uids else {}
        text, entities = build_mentions(body, members, mention_uids or [])
        # 与 api/vega/octo.py 相同的 wire format：消息体嵌在 payload，content 是裸字符串
        inner: dict = {"type": 1, "content": text}  # 1 = TEXT
        if entities:
            inner["mention"] = {"entities": entities}
        _post(
            "/v1/bot/sendMessage",
            {
                "channel_id": chat_id,
                "channel_type": 2,
                "payload": inner,
                "client_msg_no": str(uuid.uuid4()),
            },
            token,
        )
        return True
    except Exception:
        log.warning("octo 发送失败 chat=%s", chat_id, exc_info=True)
        return False