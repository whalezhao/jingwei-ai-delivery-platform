"""midscene 子进程的执行与终止。

三个必须做对的地方，每一个错了都会在生产里以奇怪的方式翻车：

1. **stdout/stderr 重定向到文件，绝不用 PIPE。** 一次执行 100 秒的日志会撑爆 64KB 的
   管道缓冲区，子进程写阻塞后再也不会退出 —— 表现是 run 永远停在 running。
2. **start_new_session=True + killpg。** node → 若干子进程 → Chromium 是一条进程链，
   只 kill 父进程会留下孤儿浏览器进程吃满内存，几次之后机器就卡了。
3. **不用 exitCode 判成败。** 有用例失败时 exitCode != 0，但执行本身是成功的。
"""

import asyncio
import os
import signal
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .parsers import read_tail
from .staging import StagedRun

TERMINATE_GRACE_SECONDS = 8
POLL_INTERVAL_SECONDS = 0.5
CANCEL_CHECK_INTERVAL_SECONDS = 2.0


@dataclass
class ExecOutcome:
    exit_code: int | None
    stdout_tail: str
    cancelled: bool = False
    timed_out: bool = False
    spawn_error: str | None = None


def _kill_process_group(pid: int, sig: int) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        # 进程已经自己退出了，或者权限不足（不该发生，因为是我们自己起的）
        pass


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """先 SIGTERM 让 node 有机会清理，宽限期后 SIGKILL 连根拔掉整个进程组。"""
    _kill_process_group(proc.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=TERMINATE_GRACE_SECONDS)
        return
    except TimeoutError:
        pass
    _kill_process_group(proc.pid, signal.SIGKILL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError:
        # 进程组都没了还不退出，只能放弃；不阻塞 worker
        pass


async def execute(
    *,
    runner_root: Path,
    staged: StagedRun,
    env: dict[str, str],
    timeout_ms: int,
    poll_cancel: Callable[[], Awaitable[bool]] | None = None,
) -> ExecOutcome:
    """跑一次 midscene-test。

    用 node_modules/.bin/midscene-test 的绝对路径而不是 npx：零网络检查、版本固定、
    不会混入 npm 的警告输出。
    """
    binary = runner_root / "node_modules" / ".bin" / "midscene-test"
    if not binary.exists():
        return ExecOutcome(None, "", spawn_error=f"找不到执行器 {binary}")

    argv = [
        str(binary),
        "--config",
        str(staged.config_path),
        "--project",
        "web",
        # 必须显式给：不指定就会写到默认的 .midscene/test-results，所有 run 互相覆盖
        "--result-dir",
        str(staged.result_dir),
    ]

    # 重定向到文件，不要 PIPE —— 见模块 docstring 第 1 条
    stdout_handle = staged.stdout_path.open("w", encoding="utf-8")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(runner_root),
            env=env,
            stdout=stdout_handle,
            stderr=asyncio.subprocess.STDOUT,
            # 让子进程自成进程组组长，取消时才能整组杀干净
            start_new_session=True,
        )
    except OSError as exc:
        stdout_handle.close()
        return ExecOutcome(None, "", spawn_error=f"启动执行器失败：{exc}")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    next_cancel_check = loop.time() + CANCEL_CHECK_INTERVAL_SECONDS
    cancelled = False
    timed_out = False

    try:
        while proc.returncode is None:
            if loop.time() > deadline:
                timed_out = True
                await _terminate(proc)
                break
            if poll_cancel is not None and loop.time() >= next_cancel_check:
                next_cancel_check = loop.time() + CANCEL_CHECK_INTERVAL_SECONDS
                if await poll_cancel():
                    cancelled = True
                    await _terminate(proc)
                    break
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        # worker 被 Ctrl-C / SIGTERM 停掉时，子进程在自己的会话组里，收不到我们的信号。
        # 不在这里主动杀，就会留下一堆孤儿 Chromium。
        await _terminate(proc)
        raise
    finally:
        stdout_handle.close()

    return ExecOutcome(
        exit_code=proc.returncode,
        stdout_tail=read_tail(staged.stdout_path),
        cancelled=cancelled,
        timed_out=timed_out,
    )


def new_run_id() -> uuid.UUID:
    return uuid.uuid4()
