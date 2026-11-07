"""每次执行的工作目录准备。

核心取舍：**不复制 midscene 项目**。node_modules 有 191MB，每次执行复制是最贵的操作，
而且换目录后 import.meta.url、相对路径、模块解析全线踩坑。做法是在 runner 包里原地跑，
只加一个 .runs/<run_id>/ 私有子目录 + 一份 run 专属 config。

目录布局：
    <runner_root>/
      package.json  node_modules/  .env          # .env 是唯一密钥来源，永不复制
      .runs/
        <run_id>/
          midscene.config.run.ts    # 生成的 config
          cases/<case_uuid>.yaml    # 暂存的选中用例，文件名就是 DB 用例 UUID
          result/<runId>/summary.json ...   # --result-dir
          midscene_run/{log,report}/  # MIDSCENE_RUN_DIR → 天然隔离
          stdout.log
      midscene_run/cache/<system_id>/    # 跨 run 共享的 AI 缓存，不随 run 目录删除
"""

import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9._-]+")

# 生成的 config 里不含任何密钥：后端读 .env 后用 subprocess 环境变量注入，
# 这样密钥不落盘到工作目录，而且 dotenv 不覆盖已存在的 env，注入值必定生效。
CONFIG_TEMPLATE = """\
// 本文件由 Aster QA 平台按次生成，请勿手工修改。
// run_id: __RUN_ID__
import type { NodeExecutionContext } from '@midscene/test';
import { defineProjectSetup, defineTestProject } from '@midscene/test/config';
import { createMidsceneNodes } from '@midscene/test/midscene';
import { PlaywrightAgent } from '@midscene/web/playwright/agent';
import { type Page, chromium } from 'playwright';

interface ProjectContext {
  page: Page;
  agent?: PlaywrightAgent;
}

const getAgent = ({ context }: NodeExecutionContext<unknown, ProjectContext>) => {
  if (!context.agent) {
    // 缓存目录由平台注入为跨 run 共享目录；不传的话会落在本次 run 的私有目录里，
    // 每次执行都是冷缓存，cacheable 永远命中不了。
    const cacheDir = process.env.QA_CACHE_DIR;
    context.agent = new PlaywrightAgent(
      context.page,
      cacheDir ? { cache: { id: 'default', cacheDir } } : undefined,
    );
  }
  return context.agent;
};

const setup = defineProjectSetup<ProjectContext>({
  name: 'web',
  async setup({ env, onTeardown }) {
    // Docker 里通常以 root 运行，Chromium 的沙箱会直接拒绝启动；容器内用环境变量显式关掉。
    // --disable-dev-shm-usage：容器默认 /dev/shm 很小，不关掉会偶发浏览器崩溃。
    const noSandbox = env.CHROMIUM_NO_SANDBOX === '1';
    const browser = await chromium.launch({
      headless: env.HEADLESS !== 'false',
      args: noSandbox ? ['--no-sandbox', '--disable-dev-shm-usage'] : [],
    });
    onTeardown(() => browser.close());
    const page = await browser.newPage();
    const context: ProjectContext = { page };
    onTeardown(async () => { await context.agent?.destroy(); });
    return context;
  },
});

export default defineTestProject<ProjectContext>({
  projects: [
    {
      name: 'web',
      setup,
      files: { include: ['__INCLUDE__'] },
      tags: __TAGS__,
      retry: __RETRY__,
      variables: __VARIABLES__,
      // 单个 run 里的用例文件串行跑：worker 已有副本级并发，这里再并发只会抢内存
      maxConcurrentFiles: 1,
    },
  ],
  nodes: [
    ...createMidsceneNodes<ProjectContext>({ agentClass: PlaywrightAgent, getAgent }),
  ],
  // bail: 0 —— 回归要跑完所有用例拿到完整分布，不能第一个失败就停
  test: {
    maxConcurrentProjects: 1,
    maxConcurrentFiles: 1,
    bail: 0,
    testTimeout: __TIMEOUT__,
  },
});
"""


@dataclass(frozen=True)
class StagedRun:
    run_id: uuid.UUID
    work_dir: Path
    config_path: Path
    cases_dir: Path
    result_dir: Path
    run_dir: Path  # MIDSCENE_RUN_DIR
    stdout_path: Path
    case_count: int

    @property
    def report_dir(self) -> Path:
        return self.run_dir / "report"

    @property
    def stats_log(self) -> Path:
        return self.run_dir / "log" / "ai-profile-stats.log"


def load_runner_env(runner_root: Path) -> dict[str, str]:
    """读 runner 的 .env。这是密钥的唯一来源，读完只存在于内存和子进程环境里。

    Docker / 生产环境通常直接把变量注入容器、没有 .env 文件 —— 这时从进程环境里
    挑出 midscene 相关的键。两处都为空才算真的没配。
    """
    env_file = runner_root / ".env"
    if not env_file.exists():
        injected = {
            key: value
            for key, value in os.environ.items()
            if key.startswith(("MIDSCENE_", "OPENAI_"))
        }
        if injected:
            return injected
        raise FileNotFoundError(
            f"缺少 {env_file}，也没有通过环境变量注入 MIDSCENE_* 配置"
        )
    env: dict[str, str] = {}
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def env_fingerprint(env: dict[str, str], variables: dict[str, str] | None = None) -> str:
    """配置指纹，用来做模型漂移归因：通过率跳变时判断是不是换了模型/配置。

    只哈希影响执行结果的键，不含 key 本身 —— 换 key 不该被当成配置变更。
    环境变量只哈希**变量名**：值可能低熵，哈希进去等于给了离线爆破的入口。
    """
    interesting = (
        "MIDSCENE_MODEL_NAME",
        "MIDSCENE_MODEL_FAMILY",
        "MIDSCENE_MODEL_BASE_URL",
        "QA_ENV_NAME",
        "QA_BASE_URL",
    )
    parts = [f"{k}={env.get(k, '')}" for k in interesting]
    parts += [f"CACHE={env.get('MIDSCENE_CACHE', '')}"]
    if variables:
        parts.append("VARS=" + ",".join(sorted(variables)))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


def case_id_from_source_path(source_path: str) -> uuid.UUID | None:
    """从 midscene 报的 sourcePath 反查我们的用例 id。

    暂存文件名就是用例 UUID，所以取 basename 去扩展名即可。
    midscene 的 caseId/documentId 是内容哈希，两份内容相同的用例会撞成同一个值，
    不能当身份用 —— 这是必须靠 sourcePath 的原因。
    """
    stem = Path(source_path).stem
    try:
        return uuid.UUID(stem)
    except ValueError:
        return None


def _json(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def render_config(
    *,
    run_id: uuid.UUID,
    include_glob: str,
    retry: int,
    tag_include: list[str],
    tag_exclude: list[str],
    base_url: str | None,
    timeout_ms: int,
    variables: dict[str, str] | None = None,
) -> str:
    merged: dict[str, str] = dict(variables or {})
    if base_url:
        # BASE_URL 由平台注入，环境变量里不允许同名（API 层已拦）
        merged["BASE_URL"] = base_url
    tags: dict[str, list[str]] = {}
    if tag_include:
        tags["include"] = tag_include
    if tag_exclude:
        tags["exclude"] = tag_exclude

    return (
        CONFIG_TEMPLATE.replace("__RUN_ID__", str(run_id))
        # 实测：files.include 是相对 cwd（projectRoot）而不是相对 config 文件所在目录，
        # 所以这里必须写从 runner 根算起的路径
        .replace("__INCLUDE__", include_glob)
        .replace("__TAGS__", _json(tags))
        .replace("__RETRY__", str(max(0, retry)))
        .replace("__VARIABLES__", _json(merged))
        .replace("__TIMEOUT__", str(timeout_ms))
    )


def stage(
    *,
    runner_root: Path,
    run_id: uuid.UUID,
    cases: list[tuple[uuid.UUID, str]],
    base_url: str | None,
    retry: int,
    tag_include: list[str] | None = None,
    tag_exclude: list[str] | None = None,
    timeout_ms: int = 600_000,
    variables: dict[str, str] | None = None,
) -> StagedRun:
    """建立 run 私有目录、落盘用例 YAML、生成 config。

    只接受非密钥变量；密钥走子进程环境，不能落到这里的 config 文件里。
    """
    work_dir = runner_root / ".runs" / str(run_id)
    cases_dir = work_dir / "cases"
    result_dir = work_dir / "result"
    run_dir = work_dir / "midscene_run"
    for path in (cases_dir, result_dir, run_dir):
        path.mkdir(parents=True, exist_ok=True)

    for case_id, yaml_text in cases:
        # 文件名来自用例 UUID，UUID 的字符集里不可能出现 `..` 或路径分隔符，
        # 所以这是安全的（这是唯一由数据决定落盘路径的地方）
        (cases_dir / f"{case_id}.yaml").write_text(yaml_text, encoding="utf-8")

    config_path = work_dir / "midscene.config.run.ts"
    config_path.write_text(
        render_config(
            run_id=run_id,
            include_glob=f".runs/{run_id}/cases/*.yaml",
            retry=retry,
            tag_include=list(tag_include or []),
            tag_exclude=list(tag_exclude or []),
            base_url=base_url,
            timeout_ms=timeout_ms,
            variables=variables,
        ),
        encoding="utf-8",
    )

    return StagedRun(
        run_id=run_id,
        work_dir=work_dir,
        config_path=config_path,
        cases_dir=cases_dir,
        result_dir=result_dir,
        run_dir=run_dir,
        stdout_path=work_dir / "stdout.log",
        case_count=len(cases),
    )


def runner_relative_run_dir(staged: StagedRun, runner_root: Path) -> str:
    """MIDSCENE_RUN_DIR 必须是**相对 cwd** 的路径。

    实测 midscene 1.15.4 用 `path.resolve(process.cwd(), getMidsceneRunDir())` 解析它，
    给绝对路径反而解析不到，所有 log 会落到 runner 根目录下的默认 midscene_run/ 里，
    多次执行的 token 统计就会混在一起。
    """
    return str(staged.run_dir.relative_to(runner_root))


def shared_cache_dir(runner_root: Path, system_id: int | None = None) -> Path:
    """跨 run 共享的 AI 缓存目录。

    默认缓存会落在 MIDSCENE_RUN_DIR/cache，也就是每个 run 的私有目录里，第二次执行
    必然冷启动。按系统分目录共享：不同系统互不干扰，同一系统的多次执行能命中缓存。
    """
    base = runner_root / "midscene_run" / "cache"
    path = base / str(system_id) if system_id is not None else base
    path.mkdir(parents=True, exist_ok=True)
    return path


def purge_run_artifacts(
    *, runner_root: Path, work_dirs: list[str], midscene_run_ids: list[str]
) -> None:
    """删掉一次执行在本机留下的全部产物。

    两类残留：
    1. ``runner/.runs/<run_id>/`` —— 每次执行的私有工作目录（用例、日志、报告）。
    2. ``runner/midscene_run/report/`` —— 执行器偶尔没被搬进私有目录的报告
       （单文件 HTML 或目录），按 runId 精确定位。

    路径都来自数据库，不能盲信 —— 只删确实落在对应根目录之下、且不等于根目录的路径。
    删不掉就留着，不影响调用方的删除结果。
    """
    runs_root = (runner_root / ".runs").resolve()
    for raw in work_dirs:
        target = Path(raw).resolve()
        if target.is_relative_to(runs_root) and target != runs_root:
            shutil.rmtree(target, ignore_errors=True)

    report_root = (runner_root / "midscene_run" / "report").resolve()
    for midscene_id in midscene_run_ids:
        # runId 是执行器生成的字符串，只接受安全字符，避免拼出 ../ 之类的路径
        if not midscene_id or not _SAFE_RUN_ID.fullmatch(midscene_id):
            continue
        for name in (f"midscene-e2e-{midscene_id}", f"midscene-e2e-{midscene_id}.html"):
            target = (report_root / name).resolve()
            if not target.is_relative_to(report_root) or target == report_root:
                continue
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink(missing_ok=True)


def relocate_report(
    runner_root: Path, staged: StagedRun, midscene_run_id: str | None
) -> Path | None:
    """把 midscene 最终的 HTML 报告搬进 run 私有目录，返回 index.html 路径。

    实测 log 与 report 的目录解析不一致：MIDSCENE_RUN_DIR 对 log/playwright 中间产物
    生效，但最后那份 `midscene-e2e-<runId>/index.html` 固定落在
    `<runner_root>/midscene_run/report/`。搬进来之后一个 run 的产物完全自包含，
    托管和按需清理都简单。
    """
    if not midscene_run_id:
        return None
    report_root = runner_root / "midscene_run" / "report"
    name = f"midscene-e2e-{midscene_run_id}"
    source_dir = report_root / name
    source_file = report_root / f"{name}.html"
    destination = staged.report_dir / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)

    if source_dir.is_dir():
        shutil.move(str(source_dir), str(destination))
        index = destination / "index.html"
        return index if index.exists() else None
    if source_file.is_file():
        # 执行器 1.17 起报告是单文件 HTML（截图内联）。仍然摆成 <run>/index.html，
        # 下游的托管路由、Markdown 转录都按这个约定走。
        destination.mkdir(parents=True, exist_ok=True)
        index = destination / "index.html"
        shutil.move(str(source_file), str(index))
        return index
    return None
