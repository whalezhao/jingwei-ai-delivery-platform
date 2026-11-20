"""调试试跑与「一个用例 = 一条 case」的纯逻辑。

这些函数都不碰数据库：拆文档、截断步骤、校验追加前缀，出错的后果分别是
统计串味、试跑范围错、已确认步骤被模型改写。
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_case import (
    _finalize,
    apply_step_options,
    extract_yaml,
    steps_of,
    truncate_steps,
    verify_prefix,
)
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

THREE = """beforeEach:
  - gotoUrl: ${BASE_URL}
cases:
  - name: A
    steps:
      - aiAct: a1
      - aiAct: a2
  - name: B
    steps:
      - aiAct: b1
      - aiAct: b2
  - name: C
    steps:
      - aiAct: c1
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


def test_truncate_stops_after_target_case():
    """case 顺序执行：跑到第 2 条的第 1 步 = 第 1 条整条 + 第 2 条 1 步，第 3 条不跑。"""
    import yaml

    text = truncate_steps(THREE, 1, 1)
    doc = yaml.safe_load(text)
    assert [len(case["steps"]) for case in doc["cases"]] == [2, 1]

    first = yaml.safe_load(truncate_steps(THREE, 1, 0))
    assert [case["name"] for case in first["cases"]] == ["A"]


def test_truncate_rejects_unknown_case_index():
    try:
        truncate_steps(SINGLE, 1, 5)
    except ValueError:
        return
    raise AssertionError("越界 case_index 应该报错")


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

# ---- 单步选项 -------------------------------------------------------------
# 缓存/深度定位只属于 aiAct，超时只属于 aiWaitFor：执行器里每个节点的 options
# 是严格对象，塞错节点会直接判输入不合法。

def test_apply_step_options_per_node():
    text = """cases:
  - name: A
    steps:
      - aiTap: 点击登录
      - aiWaitFor:
          prompt: 出现首页
      - aiAct:
          prompt: 填表单
          options:
            cacheable: false
"""
    doc = yaml.safe_load(
        apply_step_options(
            text,
            [
                {"case_index": 0, "step": 0, "deep_locate": True},
                {"case_index": 0, "step": 1, "timeout_ms": 30000},
                {"case_index": 0, "step": 2, "cache": True},
            ],
            ),
    )
    steps = doc["cases"][0]["steps"]
    # aiTap 不再接受缓存/深度定位，原样保留
    assert steps[0]["aiTap"] == "点击登录"
    # aiWaitFor 只写超时
    assert steps[1]["aiWaitFor"]["options"] == {"timeoutMs": 30000}
    # aiAct：开缓存把原来的 false 改成 true
    assert steps[2]["aiAct"]["options"] == {"cacheable": True}

    # 关掉 = 删 key；options 空了连 options 本身也不留
    off = yaml.safe_load(
        apply_step_options(
            text,
            [
                {"case_index": 0, "step": 1, "timeout_ms": None},
                {"case_index": 0, "step": 2, "cache": False},
            ],
        )
    )
    off_steps = off["cases"][0]["steps"]
    assert off_steps[1]["aiWaitFor"] == {"prompt": "出现首页"}
    assert off_steps[2]["aiAct"] == {"prompt": "填表单"}


# 模型经常把用例夹在解释文字中间，且不一定加围栏：这种回复也要能填到右侧

def test_extract_yaml_from_prose_reply():
    reply = """用例说明：
- 覆盖流程：打开首页 → 点击需求池

cases:
  - name: A
    steps:
      - aiAct: 打开首页
      - aiAssert: 出现需求池

以上请确认。"""
    text = extract_yaml(reply)
    assert text is not None
    assert yaml.safe_load(text)["cases"][0]["name"] == "A"


def test_extract_yaml_fence_wins():
    reply = "说明\n```yaml\ncases:\n  - name: B\n    steps:\n      - aiAssert: y\n```\n补充"
    text = extract_yaml(reply)
    assert "name: B" in text


def test_extract_yaml_none_when_no_yaml():
    assert extract_yaml("请提供测试账号和密码。") is None


# 规划力度 / 单步上下文 / 断言失败文案：都按「关掉就删 key」的语义写

def test_apply_step_options_effort_context_message():
    text = """cases:
  - name: A
    steps:
      - aiAct:
          prompt: 点击提交
      - aiAssert: 显示成功提示
"""
    doc = yaml.safe_load(
        apply_step_options(
            text,
            [
                {
                    "case_index": 0,
                    "step": 0,
                    "effort": "deepThink",
                    "context": "按钮在右侧抽屉底部",
                },
                {"case_index": 0, "step": 1, "message": "应看到成功提示", "context": ""},
            ],
        )
    )
    steps = doc["cases"][0]["steps"]
    assert steps[0]["aiAct"]["options"] == {
        "effort": "deepThink",
        "context": "按钮在右侧抽屉底部",
    }
    # 断言：message 是步骤级键，简写被展开成块写法；空 context 不写键
    assert steps[1]["aiAssert"] == {"prompt": "显示成功提示", "message": "应看到成功提示"}

    # balance 是默认值：选了它等于不要这个键
    doc2 = yaml.safe_load(
        apply_step_options(text, [{"case_index": 0, "step": 0, "effort": "balance"}])
    )
    assert "options" not in doc2["cases"][0]["steps"][0]["aiAct"]
