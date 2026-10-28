"""路由共享的小工具。"""

from __future__ import annotations

from ..models import Person


def person_to_out(p: Person) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "avatar": p.avatar,
        "gitlab_username": p.gitlab_username,
        "gitlab_email": p.gitlab_email,
        "octo_uid": p.octo_uid,
        "is_admin": p.is_admin,
        "llm_base_url": p.llm_base_url,
        "llm_model": p.llm_model,
        "llm_effort": p.llm_effort,
        "has_llm_key": bool(p.llm_api_key_enc),
        "has_am_key": bool(p.am_user_key_enc),
        "roles": p.roles or [],
    }
