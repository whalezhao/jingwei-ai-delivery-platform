"""测试执行通知的纯逻辑回归（不依赖数据库）。

文案和 @ 偏移算错不会报错，只会在群里发出一段看不懂的消息或 @ 不到人，
所以这里把三件事钉住：开始/结果文案、失败原因提取、UTF-16 mention 偏移。
"""

import json
from types import SimpleNamespace

from app import octo
from app.notify import build_finished_text, build_started_text

BATCH = SimpleNamespace(name="登录回归", environment_name="预发", trigger="manual")
SYSTEM = SimpleNamespace(id=1, name="用户中心", group_id=10)


def _run(**kw) -> SimpleNamespace:
    base = {
        "status": "succeeded",
        "total": 0,
        "passed": 0,
        "failed": 0,
        "not_run": 0,
        "duration_ms": 0,
        "failure_kind": None,
        "error_message": None,
        "result_dir": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _result_dir(tmp_path, error: str):
    """造一份执行器产物：summary.json 指向失败用例的 result 明细。"""
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "cases": [
                            {
                                "name": "登录",
                                "status": "failed",
                                "sourcePath": "case.yaml",
                                "attempts": [{"resultFile": "detail.json"}],
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "detail.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "node": "aiAssert",
                        "input": {"prompt": "进入工作台"},
                        "status": "failed",
                        "error": {"message": error},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return str(tmp_path)


def test_started_text_lists_systems_and_environment():
    text = build_started_text(BATCH, [(SYSTEM, [_run(status="queued")])])
    assert text.startswith("🧪 测试开始 · 登录回归")
    assert "系统：用户中心" in text
    assert "预发" in text and "手动" in text


def test_finished_text_all_green():
    runs = [_run(total=12, passed=12, duration_ms=200_000)]
    text, failed = build_finished_text(BATCH, [(SYSTEM, runs)])
    assert failed is False
    assert text.startswith("✅ 测试通过 · 登录回归")
    assert "用户中心：12/12 通过 · 3m20s" in text
    assert "失败原因" not in text


def test_finished_text_failed_includes_ai_reason(tmp_path):
    long_reason = "页面上只有用户名，没有邮箱。" * 30
    runs = [
        _run(
            status="failed",
            total=2,
            passed=1,
            failed=1,
            duration_ms=45_000,
            result_dir=_result_dir(tmp_path, long_reason),
        )
    ]
    text, failed = build_finished_text(BATCH, [(SYSTEM, runs)])
    assert failed is True
    assert text.startswith("❌ 测试失败 · 登录回归")
    assert "失败原因：" in text
    assert "· 登录：页面上只有用户名，没有邮箱。" in text
    # 单条原因截断到 160 字，整条消息不会带着几千字进群
    assert len(text) < 1000


def test_finished_text_infra_failure_uses_kind():
    runs = [_run(status="failed", failure_kind="ai_quota", error_message="upstream 429")]
    text, failed = build_finished_text(BATCH, [(SYSTEM, runs)])
    assert failed is True
    assert "模型调用额度用尽" in text


def test_mentions_use_utf16_offsets():
    uid_map = {"u1": "张三"}
    text, entities = octo.build_mentions("🧪 @张三", uid_map, ["u1"])
    assert text == "🧪 @张三"
    # 🧪 是代理对，UTF-16 里占 2 个单元，后面还有一个空格
    assert entities == [{"uid": "u1", "offset": 3, "length": 3}]


def test_mentions_prepend_missing_at_text():
    text, entities = octo.build_mentions("测试失败", {"u1": "张三"}, ["u1"])
    assert text == "@张三 测试失败"
    assert entities == [{"uid": "u1", "offset": 0, "length": 3}]