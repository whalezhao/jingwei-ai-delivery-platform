"""清理执行产物与残留记录。

每次执行会在 runner/.runs/<run_id>/ 下留一份完整产物（截图内联的 HTML 报告有几 MB）。
数据库里的指标不受影响 —— 这是刻意的设计：产物只是原始凭证，统计全走 SQL。

    # 预演，只列出会删什么
    uv run python scripts/cleanup_runs.py --days 30 --keep-per-task 5
    # 真删
    uv run python scripts/cleanup_runs.py --days 30 --keep-per-task 5 --apply
    # 清掉没有任务归属的残留执行记录（数据库行 + 产物）
    uv run python scripts/cleanup_runs.py --orphans
"""

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import AiCheck, Run, RunBatch
from app.retention import collect_artifacts
from app.runner.staging import purge_run_artifacts


async def collect(keep_days: int, keep_per_task: int) -> list[tuple[Path, str]]:
    """复用执行进程里的同一套保留策略，避免脚本和后台清理各写一份。"""
    settings = get_settings()
    async with SessionLocal() as db:
        return await collect_artifacts(
            db, settings.runner_path, keep_days, keep_per_task
        )


async def purge_orphans(apply: bool) -> None:
    """删掉没有任务归属的残留执行记录（runs + 批次 + AI 结论 + 产物）。

    正常流程不会产生这种记录 —— 删任务时会连带删执行记录。历史遗留来自更早的
    「删任务只把 runs.task_id 置空」，记录就一直挂在执行记录页里。
    """
    async with SessionLocal() as db:
        runs = list(
            (await db.execute(select(Run).where(Run.task_id.is_(None)))).scalars()
        )
        batch_ids = list(
            (await db.execute(select(RunBatch.id).where(RunBatch.task_id.is_(None)))).scalars()
        )
        print(f"无任务的执行记录：{len(runs)} 个 run，{len(batch_ids)} 个批次")
        if not apply:
            print("\n（预演模式。加 --apply 才会真正删除）")
            return

        dirs = [r.work_dir for r in runs if r.work_dir]
        midscene_ids = [r.midscene_run_id for r in runs if r.midscene_run_id]
        if runs:
            await db.execute(delete(Run).where(Run.id.in_([r.id for r in runs])))
        if batch_ids:
            await db.execute(
                delete(AiCheck).where(
                    AiCheck.entity_type == "batch", AiCheck.entity_id.in_(batch_ids)
                )
            )
            await db.execute(delete(RunBatch).where(RunBatch.id.in_(batch_ids)))
        await db.commit()

    purge_run_artifacts(
        runner_root=get_settings().runner_path,
        work_dirs=dirs,
        midscene_run_ids=midscene_ids,
    )
    print(f"已删除 {len(runs)} 个 run、{len(batch_ids)} 个批次，产物目录已清理")


async def main() -> None:
    parser = argparse.ArgumentParser(description="清理 midscene 执行产物")
    parser.add_argument("--days", type=int, default=30, help="保留最近多少天（无任务的 run）")
    parser.add_argument("--keep-per-task", type=int, default=5, help="每个任务保留最近几次")
    parser.add_argument("--orphans", action="store_true", help="只清理无任务归属的残留记录")
    parser.add_argument("--apply", action="store_true", help="真正删除；不加则只预演")
    args = parser.parse_args()

    if args.orphans:
        await purge_orphans(args.apply)
        await engine.dispose()
        return

    victims = await collect(args.days, args.keep_per_task)
    total_bytes = sum(
        f.stat().st_size for path, _ in victims for f in path.rglob("*") if f.is_file()
    )
    print(f"待清理 {len(victims)} 个执行目录，约 {total_bytes / 1024 / 1024:.1f} MB")

    for path, reason in victims:
        print(f"  {path.name}  <- {reason}")

    if not args.apply:
        print("\n（预演模式。加 --apply 才会真正删除）")
    else:
        for path, _ in victims:
            shutil.rmtree(path, ignore_errors=True)
        print(f"\n已删除 {len(victims)} 个目录")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
