"""部署触发的通知文案（纯逻辑，不依赖数据库）。"""

from types import SimpleNamespace

from app.notify import _TRIGGER, build_started_text

SYSTEM = SimpleNamespace(id=1, name="用户中心", group_id=10)


def test_trigger_enum_has_deployment_and_label():
    # CheckConstraint 加了 deployment，文案表必须同步，否则批次消息会退化成裸值
    for kind in ("manual", "regression", "rerun", "requirement", "deployment"):
        assert kind in _TRIGGER
    assert _TRIGGER["deployment"] == "部署触发"


def test_started_text_renders_deployment_label():
    batch = SimpleNamespace(name="REQ-TEST-001 登录", environment_name="测试环境", trigger="deployment")
    text = build_started_text(batch, [(SYSTEM, [SimpleNamespace()])])
    assert "部署触发" in text
    assert "测试环境" in text