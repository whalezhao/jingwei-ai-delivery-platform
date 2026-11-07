"""十项能力的纯逻辑回归测试（不依赖数据库）。"""

import json
import uuid

import pytest

from app.case_versions import diff_text
from app.coverage import compute_requirement_coverage
from app.crypto import decrypt_secret, encrypt_secret
from app.environments import validate_variable_key
from app.preflight import summarize_checks
from app.runner import parsers, staging


def test_crypto_roundtrip_and_prefix():
    token = encrypt_secret("s3cret")
    assert token.startswith("enc:v1:")
    assert decrypt_secret(token) == "s3cret"
    assert decrypt_secret("plain") == "plain"


def test_variable_key_validation():
    assert validate_variable_key("USERNAME", is_secret=False) == "USERNAME"
    assert validate_variable_key("API_TOKEN", is_secret=True) == "API_TOKEN"
    with pytest.raises(ValueError):
        validate_variable_key("BASE_URL", is_secret=False)
    with pytest.raises(ValueError):
        validate_variable_key("bad-key", is_secret=False)
    with pytest.raises(ValueError):
        validate_variable_key("NODE_OPTIONS", is_secret=True)
    with pytest.raises(ValueError):
        validate_variable_key("MIDSCENE_MODEL_API_KEY", is_secret=True)


def test_requirement_coverage_statuses():
    rows = [
        {"requirement_id": 1, "feature_id": uuid.uuid4(), "feature_name": "登录",
         "system_name": "A", "case_id": uuid.uuid4(), "status": "success",
         "failure_kind": None, "last_run_at": None},
        {"requirement_id": 1, "feature_id": uuid.uuid4(), "feature_name": "登录",
         "system_name": "A", "case_id": uuid.uuid4(), "status": None,
         "failure_kind": None, "last_run_at": None},
        {"requirement_id": 2, "feature_id": uuid.uuid4(), "feature_name": "支付",
         "system_name": "A", "case_id": uuid.uuid4(), "status": "failed",
         "failure_kind": None, "last_run_at": None},
        {"requirement_id": 3, "feature_id": uuid.uuid4(), "feature_name": "退款",
         "system_name": "A", "case_id": uuid.uuid4(), "status": "failed",
         "failure_kind": "ai_service", "last_run_at": None},
    ]
    data = compute_requirement_coverage(rows)
    by_id = {item["requirement_id"]: item for item in data["requirements"]}
    assert by_id[1]["status"] == "partial"
    assert by_id[2]["status"] == "failed"
    assert by_id[3]["status"] == "infra"
    assert data["summary"]["total"] == 3


def test_diff_marks_changes():
    diff = diff_text("a\nb\n", "a\nc\n", "old", "new")
    assert "-b" in diff and "+c" in diff
    assert diff_text("a", "a", "old", "new") == ""


def test_preflight_summary():
    checks = [
        {"ok": False, "severity": "warning", "overridable": False},
        {"ok": True, "severity": "ok", "overridable": False},
    ]
    assert summarize_checks(checks) is True
    checks.append({"ok": False, "severity": "error", "overridable": False})
    assert summarize_checks(checks) is False
    checks[-1]["overridable"] = True
    assert summarize_checks(checks) is True


def test_staging_writes_system_variables():
    config = staging.render_config(
        run_id=uuid.uuid4(),
        include_glob=".runs/x/cases/*.yaml",
        retry=1,
        tag_include=[],
        tag_exclude=[],
        base_url="https://example.test",
        timeout_ms=1000,
        variables={"USERNAME": "qa"},
    )
    assert '"USERNAME": "qa"' in config
    assert '"BASE_URL": "https://example.test"' in config


def test_shared_cache_dir_is_per_system(tmp_path):
    cache_dir = staging.shared_cache_dir(tmp_path, 42)

    assert cache_dir.is_dir()
    assert cache_dir == tmp_path / "midscene_run" / "cache" / "42"
    # 同系统复用同一目录，换系统就换目录
    assert staging.shared_cache_dir(tmp_path, 42) == cache_dir
    assert staging.shared_cache_dir(tmp_path, 43) != cache_dir

    config = staging.render_config(
        run_id=uuid.uuid4(),
        include_glob=".runs/x/cases/*.yaml",
        retry=0,
        tag_include=[],
        tag_exclude=[],
        base_url="https://example.test",
        timeout_ms=1000,
    )
    # agent 必须显式拿到共享的 cacheDir，否则缓存会落在 run 私有目录里
    assert "QA_CACHE_DIR" in config
    assert "cacheDir" in config


def test_relocate_report_handles_single_file(tmp_path):
    """执行器 1.17 起报告是单个 .html，搬进来仍要摆成 index.html。"""
    staged = staging.stage(
        runner_root=tmp_path,
        run_id=uuid.uuid4(),
        cases=[],
        base_url=None,
        retry=0,
        timeout_ms=1000,
    )
    report_root = tmp_path / "midscene_run" / "report"
    report_root.mkdir(parents=True)
    (report_root / "midscene-e2e-abc123.html").write_text("<html></html>", encoding="utf-8")

    index = staging.relocate_report(tmp_path, staged, "abc123")

    assert index is not None and index.name == "index.html"
    assert index.read_text(encoding="utf-8") == "<html></html>"
    assert not (report_root / "midscene-e2e-abc123.html").exists()


def test_collection_errors_fail_the_run(tmp_path):
    summary_dir = tmp_path / "run-1"
    error_dir = summary_dir / "project-0" / "collection-errors"
    error_dir.mkdir(parents=True)
    (error_dir / "err.json").write_text(
        json.dumps(
            {
                "kind": "collection-error",
                "error": {"message": 'Undefined project variable "BASE_URL"'},
            }
        ),
        encoding="utf-8",
    )
    (summary_dir / "summary.json").write_text(
        json.dumps(
            {
                "runId": "run-1",
                "status": "failed",
                "exitCode": 1,
                "summary": {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "collectionErrors": 1,
                },
                "projects": [
                    {
                        "name": "web",
                        "cases": [],
                        "collectionErrors": [
                            {
                                "sourcePath": ".runs/x/cases/a.yaml",
                                "errorFile": "project-0/collection-errors/err.json",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    data = parsers.parse_summary(tmp_path)
    assert data.collection_errors == 1
    assert data.collection_error_messages == [
        '.runs/x/cases/a.yaml: Undefined project variable "BASE_URL"'
    ]

    status, message = parsers.run_outcome(data, cancelled=False, timed_out=False)
    assert status == "failed"
    assert "Undefined project variable" in (message or "")


def test_run_outcome_keeps_case_failures_as_succeeded():
    # 用例失败（exitCode != 0）但结果齐全：这是「用例没过」，不是「执行失败」
    failed_cases = parsers.SummaryData(total=2, exit_code=1, midscene_status="failed")
    assert parsers.run_outcome(failed_cases, cancelled=False, timed_out=False) == (
        "succeeded",
        None,
    )
    # 全部被标签过滤掉：一个用例都没跑，但执行器正常结束
    filtered = parsers.SummaryData(total=0, exit_code=0, midscene_status="success")
    assert parsers.run_outcome(filtered, cancelled=False, timed_out=False) == (
        "succeeded",
        None,
    )
    # 一个用例都没跑且执行器失败：不能记成成功
    empty_failed = parsers.SummaryData(total=0, exit_code=1, midscene_status="failed")
    status, message = parsers.run_outcome(empty_failed, cancelled=False, timed_out=False)
    assert status == "failed"
    assert message
