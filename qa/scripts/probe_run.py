"""执行链路的离线验证脚本。

不经过 HTTP 和 worker，直接调 staging + execute + parsers，用来快速迭代执行器相关
的坑（比走一遍完整流程快得多）。用法：

    uv run python scripts/probe_run.py
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.runner import execute as exec_mod
from app.runner import parsers, staging


async def main() -> None:
    settings = get_settings()
    runner_root = settings.runner_path
    runner_env = staging.load_runner_env(runner_root)

    case_id = uuid.uuid4()
    yaml_text = (runner_root / "scripts" / "sample-case.yaml").read_text(encoding="utf-8")

    staged = staging.stage(
        runner_root=runner_root,
        run_id=case_id,
        cases=[(case_id, yaml_text)],
        base_url="http://127.0.0.1:4173/",
        retry=0,
        timeout_ms=settings.case_timeout_ms,
    )
    print(f"工作目录 {staged.work_dir}")
    print(f"include  {[l for l in staged.config_path.read_text().splitlines() if 'include' in l]}")

    env = dict(__import__("os").environ)
    env.update(runner_env)
    env["MIDSCENE_RUN_DIR"] = staging.runner_relative_run_dir(staged, runner_root)
    env.setdefault("HEADLESS", "true")
    print(f"MIDSCENE_RUN_DIR={env['MIDSCENE_RUN_DIR']}")
    print(f"模型={env.get('MIDSCENE_MODEL_NAME')} family={env.get('MIDSCENE_MODEL_FAMILY')}")

    outcome = await exec_mod.execute(
        runner_root=runner_root,
        staged=staged,
        env=env,
        timeout_ms=settings.case_timeout_ms * 3,
    )
    print(f"\n退出码={outcome.exit_code} cancelled={outcome.cancelled} timed_out={outcome.timed_out}")
    print("--- stdout 尾部 ---")
    print(outcome.stdout_tail[-1500:])

    summary = parsers.parse_summary(staged.result_dir)
    print("\n--- summary ---")
    print(f"total={summary.total} passed={summary.passed} failed={summary.failed} "
          f"duration={summary.duration_ms} ms errors={summary.errors}")
    for c in summary.cases:
        print(f"  case {c.name!r} status={c.status} source={c.source_path} "
              f"-> 用例如 UUID = {staging.case_id_from_source_path(c.source_path or '')}")

    calls = parsers.parse_stats_log(staged.stats_log)
    print(f"\n--- AI 调用 {len(calls)} 次 ---")
    if calls:
        print(f"model={calls[0].model} 合计 tokens={sum(c.total_tokens or 0 for c in calls)} "
              f"cost_ms={sum(c.cost_ms or 0 for c in calls)}")
    else:
        print(f"（{staged.stats_log} 不存在或无内容）")

    print("\n--- 隔离检查 ---")
    print(f"run 私有 log 目录: {(staged.run_dir / 'log').exists()}")
    print(f"run 私有 report 目录: {staged.report_dir.exists()} -> "
          f"{sorted(p.name for p in staged.report_dir.glob('*')) if staged.report_dir.exists() else []}")
    print(f"全局 midscene_run/log 文件数: {len(list((runner_root / 'midscene_run' / 'log').glob('*')))}")


if __name__ == "__main__":
    asyncio.run(main())
