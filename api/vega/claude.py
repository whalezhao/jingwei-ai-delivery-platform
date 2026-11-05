"""claude -p 子进程执行器（runner 节点内使用）。

输出 stream-json 解析为归一化事件流：
  {kind: "text", text}          — assistant 文本
  {kind: "thinking", text}      — 思考过程
  {kind: "text_delta"/"thinking_delta", text} — 逐帧增量（partial 模式）
  {kind: "tool_use", id, name, input} — 工具调用（id 用于子 agent 归属）
  {kind: "tool_result", content, is_error} — 工具结果
  {kind: "result", text, is_error} — 终态全文
  {kind: "raw", event}   — 其余事件原样透传（前端可选择性展示）
每个事件带 parent：子 agent（Task/Agent 工具）产生的事件携带派生它的
tool_use id（parent_tool_use_id），主会话为空串 —— UI 据此分主/子泳道。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator

from .config import get_settings


class ClaudeError(RuntimeError):
    pass


# 单事件体积上限：Write 类工具会把整文件塞进 input，超限截断防 DB 膨胀
_CAP = 32_000


def _cap(s: str, limit: int = _CAP) -> str:
    return s if len(s) <= limit else s[:limit] + "…（截断）"


def _tool_result_text(content) -> str:  # noqa: ANN001 — claude 输出两种形态：string 或 block 列表
    if isinstance(content, list):
        return "".join(x.get("text", "") for x in content if isinstance(x, dict))
    return str(content or "")


async def run_claude(
    args: list[str],
    env_extra: dict[str, str],
    *,
    cwd: str | None = None,
    prompt: str | None = None,
    partial: bool = False,
) -> AsyncIterator[dict]:
    """spawn claude -p …，逐行解析 stream-json 并归一化产出。"""
    s = get_settings()
    # effort 是解析链（人员→组→兜底）的产物，随 claude_env 下发；pop 掉避免混进子进程 env
    effort = env_extra.pop("VEGA_CLAUDE_EFFORT", "")
    argv = [s.claude_bin, "-p", "--output-format", "stream-json", "--verbose"]
    if partial:
        # 不加这个 CLI 只在整块生成完后才吐 assistant 事件，前端只能整段浮现
        argv.append("--include-partial-messages")
    if effort:
        argv += ["--effort", effort]
    argv += args
    if prompt is not None:
        argv.append(prompt)

    # 路由由 claude_env 唯一决定：宿主 shell 残留的 ANTHROPIC_*（如 cc-switch 注入的）
    # 会悄悄改道，破坏 person→group→am 兜底的解析链，先洗掉再合并
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env.update(env_extra)
    # 子 agent 的文本/思考也进流（默认只转发其工具事件），完整转录不必落节点盘
    env.setdefault("CLAUDE_CODE_FORWARD_SUBAGENT_TEXT", "1")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # Read 图片的工具结果是单行超长 base64，默认 64KiB 行上限会 ValueError 打断整轮
        limit=32 * 1024 * 1024,
    )

    async def drain_stderr() -> str:
        return (await proc.stderr.read()).decode(errors="replace") if proc.stderr else ""

    stderr_task = asyncio.create_task(drain_stderr())
    terminal = ""  # 最后一次 result 事件的摘要：退出码非 0 时它是真正的死因（stderr 常只有无关告警）
    # 本消息已经逐帧下发的块类型：整块事件仍产出（落库回放要用），但标 streamed 让直播端去重
    streamed: set[tuple[str, str]] = set()
    assert proc.stdout
    try:
        async for line in proc.stdout:
            line = line.decode(errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = evt.get("type")
            # 子 agent（Task/Agent 工具）事件带 parent_tool_use_id 指回派生它的 tool_use
            parent = str(evt.get("parent_tool_use_id") or "")
            if kind == "assistant":
                for block in evt.get("message", {}).get("content", []):
                    t = block.get("type")
                    if t == "text" and block.get("text"):
                        flag = {"streamed": True} if (parent, "text") in streamed else {}
                        yield {"kind": "text", "text": _cap(block["text"]), "parent": parent, **flag}
                    elif t == "thinking" and block.get("thinking"):
                        flag = {"streamed": True} if (parent, "thinking") in streamed else {}
                        yield {"kind": "thinking", "text": _cap(block["thinking"]), "parent": parent, **flag}
                    elif t == "tool_use":
                        yield {
                            "kind": "tool_use",
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "input": _cap(json.dumps(block.get("input", {}), ensure_ascii=False)),
                            "parent": parent,
                        }
            elif kind == "user":
                # claude 的工具结果以 user 轮回传
                for block in evt.get("message", {}).get("content", []):
                    if block.get("type") == "tool_result":
                        yield {
                            "kind": "tool_result",
                            # 并行工具/子代理的结果会乱序回来，靠 id 对上调用方
                            "tool_use_id": block.get("tool_use_id", ""),
                            "content": _cap(_tool_result_text(block.get("content")), 8_000),
                            "is_error": bool(block.get("is_error")),
                            "parent": parent,
                        }
            elif kind == "stream_event":
                # partial 模式的原生 SSE 帧。子代理目前不走增量（parent 恒空），
                # 所以增量只当主会话；未知帧（message_start 等）直接吞掉，别当 raw 落库
                inner = evt.get("event") or {}
                it = inner.get("type")
                if it in ("message_start", "message_stop"):
                    streamed.discard((parent, "text"))
                    streamed.discard((parent, "thinking"))
                elif it == "content_block_delta":
                    d = inner.get("delta") or {}
                    dt = d.get("type")
                    if dt == "text_delta" and d.get("text"):
                        streamed.add((parent, "text"))
                        yield {"kind": "text_delta", "text": d["text"], "parent": parent}
                    elif dt == "thinking_delta" and d.get("thinking"):
                        streamed.add((parent, "thinking"))
                        yield {"kind": "thinking_delta", "text": d["thinking"], "parent": parent}
            elif kind == "result":
                terminal = json.dumps(
                    {k: evt.get(k) for k in ("subtype", "terminal_reason", "num_turns", "errors")},
                    ensure_ascii=False,
                )[:500]
                yield {
                    "kind": "result",
                    "text": evt.get("result", ""),
                    "is_error": bool(evt.get("is_error")),
                    "parent": parent,
                }
            else:
                yield {"kind": "raw", "event": evt, "parent": parent}
    finally:
        if proc.returncode is None:
            proc.kill()
        stderr = await stderr_task
    rc = await proc.wait()
    if rc != 0:
        raise ClaudeError(f"claude -p 退出码 {rc}: {terminal or '(无 result 事件)'} | {stderr[-300:]}")


def plan_args(session_id: str, resume: bool) -> list[str]:
    flag = "--resume" if resume else "--session-id"
    # 问答跑在常驻仓基线目录（多个任务共用的检出），Bash 不能整体放行：
    # 一条写命令就把基线弄脏，后续 fetch/checkout 全被本地改动挡住。
    # 只留只读工具 + memory-bridge 的检索 curl（限定 agent-memory 代理地址）。
    am = get_settings().am_base_url.rstrip("/")
    return [
        "--permission-mode",
        "plan",
        "--allowedTools",
        f"Read,Grep,Glob,LS,Bash(curl *{am}/memory-bridge/*)",
        flag,
        session_id,
    ]


def dev_args(session_id: str) -> list[str]:
    return [
        "--dangerously-skip-permissions",
        "--max-turns",
        "200",
        "--session-id",
        session_id,
    ]


def _rule_path(p: str) -> str:
    """权限规则里的路径（gitignore 四分法）：家目录内用 ~/，其余绝对路径用 //。"""
    home = os.path.expanduser("~").rstrip("/")
    p = p.rstrip("/")
    if p == home:
        return "~"
    if p.startswith(home + "/"):
        return "~" + p[len(home) :]
    return "/" + p


def egm_plan_args(session_id: str, resume: bool, artifact_dir: str) -> list[str]:
    """EGM 方案轮（brainstorm / draft / resolve）。

    与 plan_args 的差别只有两处，但都是硬需求：要开子代理（六视角、评委），
    且要能写产物文件——而 --permission-mode plan 禁写，draft 会写不出 design_draft.md。
    所以这里不用 plan 模式，改用「默认模式 + 白名单」：读不限，写只给产物目录，
    Bash 仅留 memory-bridge 检索。基线仓库依旧不可写，越界写由 runner 的
    git status 断言兜底。
    """
    flag = "--resume" if resume else "--session-id"
    am = get_settings().am_base_url.rstrip("/")
    art = _rule_path(artifact_dir)
    return [
        "--allowedTools",
        ",".join(
            [
                "Read",
                "Grep",
                "Glob",
                "LS",
                "Agent",
                f"Write({art}/**)",
                f"Edit({art}/**)",
                f"Bash(curl *{am}/memory-bridge/*)",
            ]
        ),
        # EGM 支持用 CLAUDE.md 里的 `EGM artifact directory:` 指定产物目录，
        # 无头运行没有项目 CLAUDE.md，改成同一句话走 system prompt
        "--append-system-prompt",
        f"EGM artifact directory: {artifact_dir.rstrip('/')}",
        # 产物目录在 cwd（基线仓库）之外：不加 --add-dir，白名单写了也进不去
        "--add-dir",
        artifact_dir.rstrip("/"),
        # 插件目录也要显式加进工作区：EGM 的技能会 Read 自己的 docs/resources/agents
        "--add-dir",
        get_settings().egm_plugin_dir,
        "--plugin-dir",
        get_settings().egm_plugin_dir,
        flag,
        session_id,
    ]


def egm_review_args(session_id: str, artifact_dir: str) -> list[str]:
    """Goldfish 评审：只读 + 子代理，不碰仓库、不跑命令。

    写权限只给产物目录 —— 主编排要落 consolidated_review_report.md；
    评委本身在 EGM 里是只读角色，子代理继承同一白名单，但至少改不到源码与基线。
    绝不用 --dangerously-skip-permissions：EGM 明确反对拿它绕开评委的权限约束。
    --max-turns 给 120：5 阶段 × 每阶段多名评委，一轮评审就是几十次子代理调用。
    """
    art = _rule_path(artifact_dir)
    return [
        "--allowedTools",
        f"Read,Grep,Glob,LS,Agent,Write({art}/**),Edit({art}/**)",
        "--append-system-prompt",
        f"EGM artifact directory: {artifact_dir.rstrip('/')}",
        "--add-dir",
        artifact_dir.rstrip("/"),
        "--add-dir",
        get_settings().egm_plugin_dir,
        "--max-turns",
        "120",
        "--plugin-dir",
        get_settings().egm_plugin_dir,
        "--session-id",
        session_id,
    ]


def egm_job_args(session_id: str, artifact_dir: str, work_dir: str | None = None) -> list[str]:
    """EGM 项目级任务（bootstrap / drift）。

    drift 只写产物目录；bootstrap 还要在任务独占的 worktree 里落 README 树，
    所以写权限按调用方给的 work_dir 追加。一律不给 Bash：这两个技能只用读写。
    """
    art = _rule_path(artifact_dir)
    tools = ["Read", "Grep", "Glob", "LS", "Agent", f"Write({art}/**)", f"Edit({art}/**)"]
    if work_dir:
        wd = _rule_path(work_dir)
        tools += [f"Write({wd}/**)", f"Edit({wd}/**)"]
    return [
        "--allowedTools",
        ",".join(tools),
        "--append-system-prompt",
        f"EGM artifact directory: {artifact_dir.rstrip('/')}",
        "--add-dir",
        artifact_dir.rstrip("/"),
        "--add-dir",
        get_settings().egm_plugin_dir,
        "--max-turns",
        "200",
        "--plugin-dir",
        get_settings().egm_plugin_dir,
        "--session-id",
        session_id,
    ]


def review_args(session_id: str) -> list[str]:
    """MR 初审：禁一切工具——只依据内嵌事实做判定，防改动仓库或被注入触发副作用。

    工具全禁（--tools ""），但模型仍可能对着事实里的文件路径「伸手」发一次工具调用，
    拿到 No such tool available 后才继续作答。每次空调用占一轮，--max-turns 给到 6
    是为了消化它——设成 1 会直接以 error_max_turns 退出（stderr 只剩一行无关的
    unrecognized_model 告警，极易误判成鉴权/模型名问题）。
    显式给 session id：否则 CLI 新建会话时会额外请求一次「生成会话标题」，
    该后台请求用的模型名可能被代理拒绝，导致整轮 claude -p 直接退出。
    """
    return ["--tools", "", "--max-turns", "6", "--session-id", session_id]
