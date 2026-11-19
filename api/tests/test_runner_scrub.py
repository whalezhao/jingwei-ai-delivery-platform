"""git 报错会把带 token 的远端地址带进日志/通知，必须先抹掉凭据。"""

from vega.runner import _scrub


def test_scrub_removes_token_from_url():
    raw = "https://oauth2:glpat-secret-value@git.example.com/group/repo.git"
    assert "glpat-secret-value" not in _scrub(raw)
    assert _scrub(raw) == "https://oauth2:***@git.example.com/group/repo.git"


def test_scrub_leaves_plain_text_alone():
    assert _scrub("fatal: 远端不存在") == "fatal: 远端不存在"
    assert _scrub("") == ""