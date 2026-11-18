"""调试试跑与「一个用例 = 一条 case」的纯逻辑。

这些函数都不碰数据库：拆文档、截断步骤、校验追加前缀，出错的后果分别是
统计串味、试跑范围错、已确认步骤被模型改写。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_case import _finalize, steps_of, truncate_steps, verify_prefix
from app.runner.staging import case_id_from_source_path
from app.yamlcheck import split_cases

MULTI = """beforeEach:
  - gotoUrl: ${BASE_URL}
cases:
  - name: 登录
    steps:
      - aiAct: 打开登录页
      - aiAssert: 出现登录表单
  - name: 搜索
    steps:
      - aiAct: 在搜索框输入 zzz
"""

SINGLE = """beforeEach:
  - gotoUrl: ${BASE_URL}
cases:
  - name: 登录
    steps:
      - aiAct: 打开登录页
      - aiAssert: 出现登录表单
"""


def test_split_cases_keeps_document_level_keys():
    docs = split_cases(MULTI)
    assert len(docs) == 2
    for doc in docs:
        assert "gotoUrl: ${BASE_URL}" in doc
        assert doc.count("- name:") == 1


def test_split_cases_leaves_valid_single_document_alone():
    assert split_cases(SINGLE) == [SINGLE]
    # 语法坏掉时不拆，交给 validate 报错
    assert split_cases("cases: [") == ["cases: ["]


def test_truncate_steps_keeps_prefix_only():
    short = truncate_steps(SINGLE, 1)
    assert "出现登录表单" not in short
    assert "打开登录页" in short
    # 步数本来就不超过上限时原样返回，不做无意义的重排
    assert truncate_steps(SINGLE, 10) == SINGLE


def test_verify_prefix_rejects_rewritten_steps():
    appended = SINGLE.replace(
        "      - aiAssert: 出现登录表单\n", "      - aiAssert: 出现登录表单\n      - aiAct: 输入账号\n"
    )
    assert verify_prefix(SINGLE, appended)
    assert not verify_prefix(SINGLE, SINGLE.replace("打开登录页", "点击登录页"))
    assert not verify_prefix(SINGLE, "cases:\n  - name: 登录\n    steps:\n      - aiAct: 打开登录页\n")


def test_verify_prefix_ignores_empty_current():
    assert verify_prefix("", SINGLE)


def test_debug_source_path_resolves_to_no_case():
    """试跑用例不落 TestCase：文件名不是 UUID，结果落 NULL 而不是撞外键。"""
    assert case_id_from_source_path("/runner/.runs/x/cases/debug.yaml") is None


def test_steps_of_lists_every_step():
    items = steps_of(MULTI)
    assert [item["kind"] for item in items] == ["aiAct", "aiAssert", "aiAct"]
    assert items[0]["case"] == "登录" and items[2]["case"] == "搜索"
    assert items[1]["index"] == 1


def test_finalize_drops_result_when_locked_steps_change():
    reply, yaml_text, violated, diff, steps = _finalize(
        "改好了\n```yaml\n" + SINGLE.replace("打开登录页", "点开登录页") + "```\n",
        SINGLE.replace("打开登录页", "点开登录页"),
        SINGLE,
        "append",
    )
    assert violated and yaml_text is None and not steps
    assert "已丢弃" in reply
    assert diff is None


def test_finalize_reports_diff_on_append():
    appended = SINGLE.replace(
        "      - aiAssert: 出现登录表单\n", "      - aiAssert: 出现登录表单\n      - aiAct: 输入账号\n"
    )
    _reply, yaml_text, violated, diff, steps = _finalize(appended, appended, SINGLE, "append")
    assert not violated and yaml_text == appended
    assert "+      - aiAct: 输入账号" in diff
    assert len(steps) == 3