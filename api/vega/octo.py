"""octo bot API 客户端：群成员同步 + 带 @ 的消息发送。

@ 的 wire format 逻辑移植自 ~/.hermes/scripts/im_send.py（实测过的坑都在注释里）：
mention 靠 payload.mention.entities（UTF-16 offset/length 侧车）触发高亮提醒，
正文里必须出现 @昵称 文本，且要先补齐缺失的 @ 再统一算 offset。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid

from .config import get_settings

log = logging.getLogger(__name__)

_TIMEOUT = 20

# 集成中心录入的全局 bot token（落库加密，启动时解密装进来）。进程内只有 api 一个入口，
# 改配置时同步更新这里，不需要每次发请求都查库。
_global_token: str | None = None


def set_global_token(token: str | None) -> None:
    global _global_token
    _global_token = token or None


def _api_url() -> str:
    return (get_settings().octo_api_url or "").rstrip("/")


def _token() -> str:
    return _global_token or get_settings().octo_bot_token


def _tok(token: str | None) -> str:
    # 组专属 bot token 优先；None = 未配置，落全局 settings
    return token if token is not None else _token()


def configured(token: str | None = None) -> bool:
    return bool(_api_url() and _tok(token))


def _get(path: str, token: str | None) -> dict | list | None:
    req = urllib.request.Request(
        f"{_api_url()}{path}", headers={"Authorization": f"Bearer {_tok(token)}"}
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode())


def _post(path: str, payload: dict, token: str | None) -> dict:
    req = urllib.request.Request(
        f"{_api_url()}{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {_tok(token)}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode())


def list_groups(token: str | None = None) -> tuple[list[dict], str | None]:
    """bot 所在群列表。返回 (items, error)——错误必须让用户看见，否则群号选不了。

    实测响应是顶层数组（不是 {"groups": [...]}）；bot 不在任何群时返回字面 null。
    """
    if not _api_url():
        return [], "octo API 地址未配置（VEGA_OCTO_API_URL）"
    if not _tok(token):
        return [], "bot token 未配置（在集成中心填全局 bot token，或本组单独填）"
    try:
        data = _get("/v1/bot/groups", token)
    except Exception as e:  # noqa: BLE001
        return [], f"群列表拉取失败：{e}"
    raw = data.get("groups") if isinstance(data, dict) else data
    if not raw:
        return [], "该 bot 不在任何群里（先把 bot 拉进群，或检查 token）"
    return [
        {"group_no": str(g["group_no"]), "name": str(g.get("name") or "")}
        for g in raw
        if isinstance(g, dict) and g.get("group_no")
    ], None


def group_members_ex(group_no: str, token: str | None = None) -> tuple[dict[str, str], str | None]:
    """群成员 uid → 昵称，带失败原因（对配页要把原因亮出来而不是显示 0 人）。"""
    if not group_no:
        return {}, "该组未设置 octo 群号（编辑研发组里选择）"
    if not _api_url():
        return {}, "octo API 地址未配置（VEGA_OCTO_API_URL）"
    if not _tok(token):
        return {}, "bot token 未配置（本组未填且全局未配）"
    try:
        data = _get(f"/v1/bot/groups/{group_no}/members", token)
    except urllib.error.HTTPError as e:
        return {}, f"群成员拉取失败 HTTP {e.code}（群号不对或 bot 不在该群）"
    except Exception as e:  # noqa: BLE001
        return {}, f"群成员拉取失败：{e}"
    members = data.get("members", data) if isinstance(data, dict) else data
    out: dict[str, str] = {}
    for m in members or []:
        if not (isinstance(m, dict) and m.get("uid") and m.get("name")):
            continue
        # bot 是 AI 不是人类：robot 标志为准，uid 带 _bot 后缀兜底
        if m.get("robot") or str(m["uid"]).endswith("_bot"):
            continue
        out[str(m["uid"])] = str(m["name"])
    return out, (None if out else "群成员列表为空（群号不对或 bot 不在该群）")


def group_members(group_no: str, token: str | None = None) -> dict[str, str]:
    """兼容包装：同步任务等旧调用方只关心字典，失败按空处理。"""
    return group_members_ex(group_no, token)[0]


def _utf16len(s: str) -> int:
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in s)


def build_mentions(body: str, uid_to_name: dict[str, str], mention_uids: list[str]) -> tuple[str, list[dict]]:
    """把要 @ 的 uid 变成 (正文, entities)。查不到昵称的宁可不 @（避免正文出现 uid 天书）。"""
    targets = [(u, uid_to_name.get(u)) for u in mention_uids if u]
    matched = [(u, nm) for u, nm in targets if nm]
    missing = [(u, nm) for u, nm in matched if f"@{nm}" not in body]
    text = (" ".join(f"@{nm}" for _, nm in missing) + " " + body) if missing else body

    entities = []
    pos = 0
    for uid, name in matched:
        token = f"@{name}"
        idx = text.find(token, pos)
        if idx < 0:
            continue
        entities.append(
            {
                "uid": uid,
                "offset": _utf16len(text[:idx]),
                "length": _utf16len(token),
            }
        )
        pos = idx + len(token)
    return text, entities


def send_text(
    chat_id: str,
    body: str,
    mention_uids: list[str] | None = None,
    token: str | None = None,
) -> bool:
    """发文本 + @。未配置 octo 时静默返回 False（notify 层负责留痕）。"""
    if not configured(token) or not chat_id:
        return False
    try:
        members = group_members(chat_id, token) if mention_uids else {}
        text, entities = build_mentions(body, members, mention_uids or [])
        # 实测 wire format（对齐 hermes im_send.py）：端点是 /v1/bot/sendMessage，
        # 消息体嵌在 payload 里，content 是裸字符串不是对象；Vega 只往群里发 → channel_type=2
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
    except Exception:  # noqa: BLE001
        log.warning("octo send_text failed", exc_info=True)
        return False
