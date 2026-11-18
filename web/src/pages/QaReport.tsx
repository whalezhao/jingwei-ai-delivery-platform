import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, Pause, Play, RefreshCw, X } from "lucide-react";
import Page from "../Page";
import type { MsgKey } from "../i18n";
import {
  dur,
  qa,
  qaBase,
  type QaReportAttempt,
  type QaReportCase,
  type QaReportData,
  type QaReportExecution,
  type QaReportKeyframe,
  type QaReportProject,
  type QaReportStep,
  type QaReportTask,
  type QaRunDetail,
} from "../qa";
import Badge, { type BadgeTone } from "../ui/Badge";
import { Button } from "../components/motion/button/index";
import { toast } from "../ui/toast";

/** 原始报告里的状态值 → 平台语义色 + 中文标签 */
const ST: Record<string, [BadgeTone, MsgKey]> = {
  passed: ["success", "qa.report.stPassed"],
  success: ["success", "qa.report.stPassed"],
  succeeded: ["success", "qa.report.stPassed"],
  failed: ["danger", "qa.report.stFailed"],
  warning: ["warning", "qa.report.stWarning"],
  "not-run": ["neutral", "qa.report.stNotRun"],
  skipped: ["neutral", "qa.report.stNotRun"],
};

const PHASE: Record<string, MsgKey> = {
  beforeEach: "qa.report.phaseBefore",
  steps: "qa.report.phaseSteps",
  afterEach: "qa.report.phaseAfter",
};

/** 报告还没落盘的执行状态：轮询等它生成 */
const ACTIVE = new Set(["queued", "preparing", "running", "parsing"]);

const DOT: Record<string, string> = {
  passed: "bg-success",
  failed: "bg-danger",
  warning: "bg-warning",
};

/** 报告里的自由文本可能带执行器厂商名与官网链接：文案统一改称「执行器」，
 *  但 MIDSCENE_MODEL_FAMILY 这类全大写配置键要原样保留，改了用户就搜不到。 */
const clean = (text: string) =>
  text
    .replace(/https?:\/\/\S*midscene\S*/gi, "（链接已省略）")
    .replace(/midscene/gi, (match: string, offset: number, whole: string) => {
      let start = offset;
      while (start > 0 && /[A-Za-z0-9_]/.test(whole[start - 1])) start -= 1;
      let end = offset + match.length;
      while (end < whole.length && /[A-Za-z0-9_]/.test(whole[end])) end += 1;
      const token = whole.slice(start, end);
      return token.includes("_") && token === token.toUpperCase() ? match : "执行器";
    });

const num = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("en-US");

const clock = (value: number | string | null | undefined) => {
  if (value == null) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleTimeString(undefined, { hour12: false });
};

const stamp = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString() : "—";

const caseKey = (item: QaReportCase) => item.caseUuid || item.id;

type PlayFrame = { frame: QaReportKeyframe; step: QaReportStep };

const playFrames = (item: QaReportCase): PlayFrame[] =>
  item.steps.flatMap((step) => step.keyframes.map((frame) => ({ frame, step })));

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  const hit = ST[status];
  return <Badge tone={hit?.[0] ?? "neutral"}>{hit ? t(hit[1]) : status || "—"}</Badge>;
}

/** 折叠的等宽 JSON / 文本块；空值直接不渲染，避免满屏 "{}" */
function JsonBlock({ label, value }: { label: string; value: unknown }) {
  if (value == null) return null;
  const text =
    typeof value === "string" ? clean(value) : clean(JSON.stringify(value, null, 2) || "");
  if (!text.trim() || text === "{}" || text === "[]") return null;
  return (
    <details className="group">
      <summary className="cursor-pointer list-none text-[11.5px] text-muted-foreground transition-colors select-none hover:text-foreground">
        {label}
      </summary>
      <pre className="mt-1 max-h-64 overflow-auto rounded-md border border-border/60 bg-muted/40 p-2 font-mono text-[11px] leading-relaxed break-words whitespace-pre-wrap text-foreground/80">
        {text}
      </pre>
    </details>
  );
}

/** 单次模型调用的用量：能读出数字就压成一行，读不出就按原样折叠 */
function UsageLine({ usage }: { usage: unknown }) {
  const { t } = useTranslation();
  if (!usage || typeof usage !== "object") return <JsonBlock label={t("qa.report.usage")} value={usage} />;
  const u = usage as Record<string, unknown>;
  const model = [u.model_name, u.response_model_name].find((v) => typeof v === "string") as
    | string
    | undefined;
  const parts: string[] = [];
  if (model) parts.push(clean(model));
  if (typeof u.prompt_tokens === "number") parts.push(`prompt ${num(u.prompt_tokens)}`);
  if (typeof u.completion_tokens === "number") parts.push(`completion ${num(u.completion_tokens)}`);
  if (typeof u.total_tokens === "number") parts.push(`total ${num(u.total_tokens)}`);
  if (typeof u.time_cost === "number") parts.push(`${num(u.time_cost)}ms`);
  if (!parts.length) return <JsonBlock label={t("qa.report.usage")} value={usage} />;
  return (
    <div className="mt-1 text-[11px] text-muted-foreground tabular-nums">
      {t("qa.report.usage")} · {parts.join(" · ")}
    </div>
  );
}

function TaskRow({ task }: { task: QaReportTask }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-md border border-border/60 bg-card px-2.5 py-1.5">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-[11px] text-muted-foreground tabular-nums">{task.index}</span>
        <span className="text-[12px] font-medium text-foreground">{clean(task.title)}</span>
        <StatusBadge status={task.status} />
        <span className="ml-auto text-[11px] text-muted-foreground tabular-nums">
          {dur(task.durationMs)}
        </span>
      </div>
      {task.detail && (
        <div className="mt-0.5 text-[12px] whitespace-pre-wrap text-foreground/85">
          {clean(task.detail)}
        </div>
      )}
      {task.error && (
        <div className="mt-1 rounded bg-danger/5 px-2 py-1 text-[11.5px] whitespace-pre-wrap text-danger">
          {clean(task.error)}
        </div>
      )}
      {task.thought && (
        <div className="mt-1 text-[11.5px] whitespace-pre-wrap text-muted-foreground">
          {clean(task.thought)}
        </div>
      )}
      <div className="mt-1 space-y-1 empty:mt-0">
        <JsonBlock label={t("qa.report.reasoning")} value={task.reasoning} />
        <UsageLine usage={task.usage} />
        <JsonBlock label={t("qa.report.param")} value={task.param} />
        <JsonBlock label={t("qa.report.output")} value={task.output} />
        <JsonBlock label={t("qa.report.log")} value={task.log} />
      </div>
    </div>
  );
}

function ExecutionBlock({ execution }: { execution: QaReportExecution }) {
  return (
    <div className="rounded-lg border border-border/70 bg-muted/25 p-2.5">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-[12px] font-medium text-foreground">{clean(execution.name)}</span>
        <StatusBadge status={execution.status} />
        <span className="ml-auto text-[11px] text-muted-foreground tabular-nums">
          {dur(execution.durationMs)}
        </span>
      </div>
      {execution.tasks.length > 0 && (
        <div className="mt-1.5 space-y-1.5">
          {execution.tasks.map((task) => (
            <TaskRow key={`${execution.id}-${task.index}`} task={task} />
          ))}
        </div>
      )}
    </div>
  );
}

function StepDetail({ step }: { step: QaReportStep }) {
  const { t } = useTranslation();
  return (
    <div className="mt-2 space-y-2">
      {step.thought && (
        <p className="m-0 text-[12.5px] whitespace-pre-wrap text-foreground/80">
          {clean(step.thought)}
        </p>
      )}
      {step.executions.map((execution) => (
        <ExecutionBlock key={execution.id} execution={execution} />
      ))}
      <JsonBlock label={t("qa.report.param")} value={step.param} />
      <JsonBlock label={t("qa.report.output")} value={step.outputData} />
      <JsonBlock label={t("qa.report.metrics")} value={step.metrics} />
    </div>
  );
}

/** AI 明细视图里的单步：文本 + 折叠的执行明细，画面交给「回放」页 */
function StepRow({
  step,
  onSeek,
}: {
  step: QaReportStep;
  onSeek: (stepId: string) => void;
}) {
  const { t } = useTranslation();
  // 失败步骤直接摊开明细：明细页的第一诉求就是「为什么挂了」
  const [open, setOpen] = useState(step.status === "failed");
  const hasDetail =
    step.executions.length > 0 ||
    step.thought !== "" ||
    step.param != null ||
    step.outputData != null ||
    step.metrics != null;
  return (
    <div className="relative">
      <span
        className={`absolute top-[6px] -left-[17px] size-2 rounded-full ring-4 ring-card ${
          DOT[step.status] || "bg-muted-foreground/40"
        }`}
      />
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span className="text-[11px] text-muted-foreground tabular-nums">{step.index}</span>
        <span className="rounded bg-muted px-1.5 py-px text-[10.5px] text-muted-foreground">
          {PHASE[step.phase] ? t(PHASE[step.phase]) : step.phase}
        </span>
        <span className="text-[13px] font-medium text-foreground">{clean(step.title)}</span>
        <StatusBadge status={step.status} />
        {step.continuedAfterError && (
          <span className="text-[11px] text-warning">{t("qa.report.continued")}</span>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground tabular-nums">
          {dur(step.durationMs)} · {clock(step.startedAt)}
        </span>
      </div>

      {step.summary && (
        <p className="mt-1 mb-0 text-[12.5px] whitespace-pre-wrap text-muted-foreground">
          {clean(step.summary)}
        </p>
      )}
      {step.error && (
        <div className="mt-1.5 rounded-md border border-danger/20 bg-danger/5 px-2.5 py-1.5 text-[11.5px] whitespace-pre-wrap text-danger">
          {clean(step.error)}
        </div>
      )}
      {step.notRunReason && (
        <div className="mt-1 text-[11.5px] text-muted-foreground">
          {t("qa.report.notRunReason")}：{clean(step.notRunReason)}
        </div>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
        {hasDetail && (
          <button
            type="button"
            onClick={() => setOpen(!open)}
            className="text-[11.5px] text-primary hover:underline"
          >
            {open ? t("qa.report.hideDetail") : t("qa.report.showDetail")}
          </button>
        )}
        {step.keyframes.length > 0 && (
          <button
            type="button"
            onClick={() => onSeek(step.id)}
            className="text-[11.5px] text-muted-foreground hover:text-foreground hover:underline"
          >
            {t("qa.report.seekPlayback")} · {step.keyframes.length}
          </button>
        )}
      </div>

      {open && hasDetail && <StepDetail step={step} />}
    </div>
  );
}

function Attempts({ attempts }: { attempts: QaReportAttempt[] }) {
  const { t } = useTranslation();
  if (attempts.length < 2) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border/70 bg-muted/20 px-4 py-1.5 text-[11.5px] text-muted-foreground">
      <span>{t("qa.report.attempts")}</span>
      {attempts.map((attempt) => (
        <span key={attempt.index} className="inline-flex items-center gap-1.5">
          {t("qa.report.attemptN", { n: attempt.index })}
          <StatusBadge status={attempt.status} />
          <span className="tabular-nums">{dur(attempt.durationMs)}</span>
        </span>
      ))}
    </div>
  );
}

/** 回放：大画面 + 步骤章节 + 胶片条。测试人员确认执行过程的主入口。 */
function Playback({
  frames,
  rid,
  index,
  onIndex,
  onZoom,
}: {
  frames: PlayFrame[];
  rid: string;
  index: number;
  onIndex: (next: number) => void;
  onZoom: (index: number) => void;
}) {
  const { t } = useTranslation();
  const total = frames.length;
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(4);
  const stripRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLButtonElement>(null);

  // 进页面即循环回放：到最后一帧回到第一帧，直到用户主动暂停
  useEffect(() => {
    if (!playing || total <= 1) return;
    const timer = window.setTimeout(
      () => onIndex(index >= total - 1 ? 0 : index + 1),
      900 / speed,
    );
    return () => window.clearTimeout(timer);
  }, [playing, index, total, speed, onIndex]);

  // 胶片条跟随当前帧横向滚动，避免播放时跑出可视区
  useEffect(() => {
    const strip = stripRef.current;
    const el = activeRef.current;
    if (!strip || !el) return;
    strip.scrollTo({
      left: el.offsetLeft - strip.clientWidth / 2 + el.clientWidth / 2,
      behavior: "smooth",
    });
  }, [index]);

  const current = frames[Math.min(index, total - 1)];
  if (!current) return null;

  const status = ST[current.step.status];

  // 步骤章节：按帧顺序分组，记录每步的起始帧
  const chapters: { step: QaReportStep; start: number; count: number }[] = [];
  frames.forEach((item, i) => {
    const last = chapters[chapters.length - 1];
    if (last && last.step.id === item.step.id) last.count += 1;
    else chapters.push({ step: item.step, start: i, count: 1 });
  });

  // 第一个失败步骤的最后一帧：最值得看的一帧
  const failIndex = frames.findIndex(
    (item, i) =>
      item.step.status === "failed" &&
      (i === frames.length - 1 || frames[i + 1].step.id !== item.step.id),
  );

  const seek = (next: number) => {
    setPlaying(false);
    onIndex(next);
  };

  return (
    <div id="qa-playback" className="overflow-hidden rounded-xl border border-border bg-card">
      <button
        type="button"
        onClick={() => onZoom(index)}
        aria-label={clean(current.frame.label)}
        title={t("qa.report.lightboxHint")}
        className="block w-full cursor-zoom-in bg-black"
      >
        <img
          key={current.frame.id}
          src={`${qaBase}/reports/${rid}/markdown/${current.frame.url}`}
          alt={clean(current.frame.label)}
          className="mx-auto h-[clamp(220px,42vh,440px)] w-full object-contain"
        />
      </button>

      <div className="space-y-2 px-3 py-2.5">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[12px]">
          <span className="text-muted-foreground tabular-nums">#{current.step.index}</span>
          <span className="min-w-0 truncate font-medium text-foreground">
            {clean(current.step.title)}
          </span>
          {status && <StatusBadge status={current.step.status} />}
          <span className="ml-auto text-[11px] text-muted-foreground tabular-nums">
            {clean(current.frame.label)} · {clock(current.frame.time)}
          </span>
        </div>

        {current.step.error && (
          <p className="m-0 line-clamp-2 text-[11.5px] whitespace-pre-wrap text-danger">
            {clean(current.step.error)}
          </p>
        )}

        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label={t("qa.report.prevFrame")}
            disabled={index === 0}
            onClick={() => seek(Math.max(index - 1, 0))}
            className="grid size-8 place-items-center rounded-full bg-muted text-foreground transition-colors hover:bg-muted/70 disabled:opacity-30"
          >
            <ChevronLeft className="size-4" />
          </button>
          <button
            type="button"
            aria-label={playing ? t("qa.report.pause") : t("qa.report.play")}
            disabled={total <= 1}
            onClick={() => setPlaying((value) => !value)}
            className="grid size-9 place-items-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
          </button>
          <button
            type="button"
            aria-label={t("qa.report.nextFrame")}
            disabled={index >= total - 1}
            onClick={() => seek(Math.min(index + 1, total - 1))}
            className="grid size-8 place-items-center rounded-full bg-muted text-foreground transition-colors hover:bg-muted/70 disabled:opacity-30"
          >
            <ChevronRight className="size-4" />
          </button>
          <input
            type="range"
            min={0}
            max={total - 1}
            value={index}
            onChange={(event) => seek(Number(event.target.value))}
            aria-label={t("qa.report.playerTitle")}
            className="min-w-0 flex-1 accent-primary"
          />
          <span className="w-14 text-right text-[11px] text-muted-foreground tabular-nums">
            {index + 1} / {total}
          </span>
          <button
            type="button"
            aria-label={t("qa.report.speed")}
            onClick={() => setSpeed((value) => (value === 4 ? 1 : value === 1 ? 2 : 4))}
            className="rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground tabular-nums transition-colors hover:text-foreground"
          >
            {speed}x
          </button>
          {failIndex >= 0 && (
            <button
              type="button"
              onClick={() => seek(failIndex)}
              className="rounded-md border border-danger/30 px-1.5 py-0.5 text-[11px] text-danger transition-colors hover:bg-danger/5"
            >
              {t("qa.report.jumpFailure")}
            </button>
          )}
        </div>

        <div className="flex flex-wrap gap-1.5">
          {chapters.map(({ step, start, count }) => {
            const active = step.id === current.step.id;
            return (
              <button
                key={step.id}
                type="button"
                onClick={() => seek(start)}
                aria-current={active ? "step" : undefined}
                className={`inline-flex max-w-56 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
                  active
                    ? "border-primary/40 bg-primary/10 text-foreground"
                    : "border-border bg-card text-muted-foreground hover:text-foreground"
                }`}
              >
                <span
                  className={`size-1.5 shrink-0 rounded-full ${
                    DOT[step.status] || "bg-muted-foreground/40"
                  }`}
                />
                <span className="truncate">
                  {step.index} {clean(step.title)}
                </span>
                <span className="shrink-0 text-muted-foreground/70 tabular-nums">{count}</span>
              </button>
            );
          })}
        </div>

        <div ref={stripRef} className="relative flex gap-1.5 overflow-x-auto pb-1">
          {frames.map((item, i) => (
            <Fragment key={item.frame.id}>
              {i > 0 && frames[i - 1].step.id !== item.step.id && (
                <span aria-hidden className="w-px shrink-0 self-stretch bg-border" />
              )}
              <button
                ref={i === index ? activeRef : undefined}
                type="button"
                onClick={() => seek(i)}
                title={`#${item.step.index} ${clean(item.step.title)}`}
                className={`shrink-0 overflow-hidden rounded border transition-shadow ${
                  i === index
                    ? "border-primary ring-2 ring-primary/40"
                    : "border-border opacity-70 hover:opacity-100"
                }`}
              >
                <img
                  src={`${qaBase}/reports/${rid}/markdown/${item.frame.url}`}
                  alt=""
                  loading="lazy"
                  className="aspect-video w-24 object-cover"
                />
              </button>
            </Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}

/** 回放视图里的用例：标题 + 播放器 */
function PlaybackCase({
  item,
  rid,
  index,
  onIndex,
  onZoom,
}: {
  item: QaReportCase;
  rid: string;
  index: number;
  onIndex: (next: number) => void;
  onZoom: (index: number) => void;
}) {
  const { t } = useTranslation();
  const frames = playFrames(item);
  return (
    <section id={`play-${caseKey(item)}`}>
      <header className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <StatusBadge status={item.status} />
        <h3 className="m-0 min-w-0 truncate text-[14px] font-semibold text-foreground">
          {clean(item.name)}
        </h3>
        <span className="ml-auto flex items-center gap-3 text-[11.5px] text-muted-foreground tabular-nums">
          <span>
            {t("qa.report.keyframes")} {frames.length}
          </span>
          <span>{dur(item.durationMs)}</span>
        </span>
      </header>
      <Attempts attempts={item.attempts} />
      {frames.length === 0 ? (
        <div className="rounded-xl border border-border bg-card py-10 text-center text-[12.5px] text-muted-foreground">
          {t("qa.report.noFrames")}
        </div>
      ) : (
        <Playback
          frames={frames}
          rid={rid}
          index={Math.min(index, frames.length - 1)}
          onIndex={onIndex}
          onZoom={onZoom}
        />
      )}
    </section>
  );
}

function CaseBlock({
  item,
  project,
  onSeek,
}: {
  item: QaReportCase;
  project: QaReportProject;
  onSeek: (stepId: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <section
      id={`case-${caseKey(item)}`}
      className="overflow-hidden rounded-xl border border-border bg-card"
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-4 py-3">
        <StatusBadge status={item.status} />
        <h3 className="m-0 min-w-0 truncate text-[14px] font-semibold text-foreground">
          {clean(item.name)}
        </h3>
        <span className="ml-auto flex items-center gap-3 text-[11.5px] text-muted-foreground tabular-nums">
          {project.shardCount ? (
            <span>{t("qa.report.shard", { i: (project.shardIndex ?? 0) + 1, n: project.shardCount })}</span>
          ) : null}
          <span>
            {t("qa.report.steps")} {item.steps.length}
          </span>
          <span>{dur(item.durationMs)}</span>
        </span>
      </header>
      <Attempts attempts={item.attempts} />
      {item.steps.length === 0 ? (
        <div className="px-4 py-6 text-[12.5px] text-muted-foreground">{t("common.noData")}</div>
      ) : (
        <div className="relative space-y-4 px-4 py-4 pl-9 before:absolute before:top-5 before:bottom-5 before:left-[19px] before:w-px before:bg-border">
          {item.steps.map((step) => (
            <StepRow key={step.id} step={step} onSeek={onSeek} />
          ))}
        </div>
      )}
    </section>
  );
}

function Lightbox({
  frames,
  index,
  rid,
  onIndex,
  onClose,
}: {
  frames: QaReportKeyframe[];
  index: number;
  rid: string;
  onIndex: (next: number) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowRight") onIndex(Math.min(index + 1, frames.length - 1));
      else if (event.key === "ArrowLeft") onIndex(Math.max(index - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [frames.length, index, onIndex, onClose]);

  const frame = frames[index];
  if (!frame) return null;
  return (
    <div
      className="fixed inset-0 z-[80] grid place-items-center bg-black/72 p-6 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={clean(frame.label)}
      onClick={onClose}
    >
      <figure
        className="m-0 flex max-h-full max-w-[min(1280px,94vw)] flex-col items-center gap-2"
        onClick={(event) => event.stopPropagation()}
      >
        <img
          src={`${qaBase}/reports/${rid}/markdown/${frame.url}`}
          alt={clean(frame.label)}
          className="max-h-[84vh] w-auto max-w-full rounded-lg border border-white/10 object-contain"
        />
        <figcaption className="flex items-center gap-3 text-[12px] text-white/75 tabular-nums">
          <span>{clean(frame.label)}</span>
          <span>{clock(frame.time)}</span>
          <span>
            {index + 1} / {frames.length}
          </span>
        </figcaption>
      </figure>
      <button
        type="button"
        aria-label={t("common.close")}
        onClick={onClose}
        className="absolute top-5 right-5 grid size-9 place-items-center rounded-full bg-white/10 text-white transition-colors hover:bg-white/20"
      >
        <X className="size-4" />
      </button>
      {frames.length > 1 && (
        <>
          <button
            type="button"
            aria-label="prev"
            disabled={index === 0}
            onClick={(event) => {
              event.stopPropagation();
              onIndex(Math.max(index - 1, 0));
            }}
            className="absolute top-1/2 left-4 grid size-9 -translate-y-1/2 place-items-center rounded-full bg-white/10 text-white transition-colors hover:bg-white/20 disabled:opacity-30"
          >
            <ChevronLeft className="size-5" />
          </button>
          <button
            type="button"
            aria-label="next"
            disabled={index >= frames.length - 1}
            onClick={(event) => {
              event.stopPropagation();
              onIndex(Math.min(index + 1, frames.length - 1));
            }}
            className="absolute top-1/2 right-4 grid size-9 -translate-y-1/2 place-items-center rounded-full bg-white/10 text-white transition-colors hover:bg-white/20 disabled:opacity-30"
          >
            <ChevronRight className="size-5" />
          </button>
        </>
      )}
    </div>
  );
}

/** 一行摘要：看报告时先要的就是「过没过、跑了多久」 */
function Summary({ stats }: { stats: QaReportData["stats"] }) {
  const { t } = useTranslation();
  return (
    <div className="mb-3 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[12px] text-muted-foreground">
      <span className="tabular-nums">
        <b className="text-[15px] font-semibold text-success">{num(stats.passed)}</b>{" "}
        {t("qa.report.passed")}
      </span>
      <span className="tabular-nums">
        <b
          className={`text-[15px] font-semibold ${
            stats.failed > 0 ? "text-danger" : "text-foreground"
          }`}
        >
          {num(stats.failed)}
        </b>{" "}
        {t("qa.report.failed")}
      </span>
      {stats.notRun > 0 && (
        <span className="tabular-nums">
          <b className="text-[15px] font-semibold text-foreground">{num(stats.notRun)}</b>{" "}
          {t("qa.report.notRun")}
        </span>
      )}
      <span className="tabular-nums">
        {t("qa.report.duration")} {dur(stats.durationMs)}
      </span>
      <span className="tabular-nums">
        {t("qa.report.steps")} {num(stats.steps)}
      </span>
      <span className="tabular-nums">
        {t("qa.report.keyframes")} {num(stats.keyframes)}
      </span>
    </div>
  );
}

/** 模型用量：默认收起，属于「偶尔查一次」的信息 */
function UsageDetails({ stats }: { stats: QaReportData["stats"] }) {
  const { t } = useTranslation();
  if (stats.models.length === 0) return null;
  const calls = stats.models.reduce((sum, row) => sum + (row.calls || 0), 0);
  return (
    <details className="mt-4 overflow-hidden rounded-xl border border-border bg-card">
      <summary className="cursor-pointer px-4 py-2 text-[11.5px] text-muted-foreground select-none hover:text-foreground">
        {t("qa.report.usageTitle")} · {num(calls)} {t("qa.report.calls")} ·{" "}
        {num(stats.tokens.total)} {t("qa.report.tokens")} · {dur(stats.modelCallTimeMs)}
      </summary>
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr className="border-b border-border text-left text-[11px] text-muted-foreground">
              <th className="px-3 py-2 font-medium">{t("qa.report.model")}</th>
              <th className="px-3 py-2 font-medium">{t("qa.report.calls")}</th>
              <th className="px-3 py-2 font-medium">prompt</th>
              <th className="px-3 py-2 font-medium">cached</th>
              <th className="px-3 py-2 font-medium">completion</th>
              <th className="px-3 py-2 font-medium">total</th>
              <th className="px-3 py-2 font-medium">{t("qa.report.duration")}</th>
            </tr>
          </thead>
          <tbody>
            {stats.models.map((row) => (
              <tr key={row.model} className="border-b border-border/60 last:border-0">
                <td className="px-3 py-1.5">
                  <div className="truncate text-foreground">{clean(row.model)}</div>
                  {row.description && (
                    <div className="truncate text-[11px] text-muted-foreground">
                      {clean(row.description)}
                    </div>
                  )}
                </td>
                <td className="px-3 py-1.5 tabular-nums">{num(row.calls)}</td>
                <td className="px-3 py-1.5 tabular-nums">{num(row.promptTokens)}</td>
                <td className="px-3 py-1.5 tabular-nums">{num(row.cachedInput)}</td>
                <td className="px-3 py-1.5 tabular-nums">{num(row.completionTokens)}</td>
                <td className="px-3 py-1.5 tabular-nums">{num(row.totalTokens)}</td>
                <td className="px-3 py-1.5 tabular-nums">{dur(row.timeCost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export default function QaReport() {
  const { t } = useTranslation();
  const { rid } = useParams();
  const [run, setRun] = useState<QaRunDetail | null>(null);
  const [report, setReport] = useState<QaReportData | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState<{ frames: QaReportKeyframe[]; index: number } | null>(null);
  const [view, setView] = useState<"play" | "detail">("play");
  const [playCase, setPlayCase] = useState("");
  const [playIndex, setPlayIndex] = useState(0);

  const load = useCallback(async (id: string, silent = false) => {
    if (!silent) setState("loading");
    try {
      const [detail, structured] = await Promise.all([
        qa<QaRunDetail>(`/api/runs/${id}`),
        qa<{ available: boolean; data: QaReportData | null }>(
          `/api/runs/${id}/report-structured`,
        ),
      ]);
      setRun(detail);
      if (structured.available && structured.data) {
        setReport(structured.data);
        setState("ready");
        // 没有关键帧就没什么可回放的，直接落到明细
        if (structured.data.stats.keyframes === 0) setView("detail");
      } else {
        setReport(null);
        setState("empty");
      }
    } catch (e: any) {
      setError(e.message);
      setState("error");
    }
  }, []);

  useEffect(() => {
    if (rid) void load(rid);
  }, [rid, load]);

  // 执行中的报告还没生成：5s 轮询，进终态后自然停下
  useEffect(() => {
    if (!rid || !run || !ACTIVE.has(run.status)) return;
    const timer = window.setTimeout(() => void load(rid, true), 5000);
    return () => window.clearTimeout(timer);
  }, [rid, run, load]);

  const openOriginal = async () => {
    if (!rid) return;
    try {
      const r = await qa<{ url: string }>(`/api/runs/${rid}/report-url`);
      window.open(r.url, "_blank", "noopener");
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const onZoom = useCallback(
    (frames: QaReportKeyframe[], index: number) => setZoom({ frames, index }),
    [],
  );
  const closeZoom = useCallback(() => setZoom(null), []);

  const cases: { item: QaReportCase; project: QaReportProject }[] =
    report?.projects.flatMap((project) =>
      project.cases.map((item) => ({ item, project })),
    ) ?? [];

  // 默认看失败用例：出报告时最常问的就是「哪条挂了」
  const selected =
    cases.find(({ item }) => caseKey(item) === playCase) ??
    cases.find(({ item }) => item.status === "failed") ??
    cases[0];
  const selectedFrames = selected ? playFrames(selected.item) : [];

  const seek = (key: string, stepId?: string) => {
    const hit = cases.find(({ item }) => caseKey(item) === key);
    if (!hit) return;
    const frames = playFrames(hit.item);
    const at = stepId ? Math.max(frames.findIndex((f) => f.step.id === stepId), 0) : 0;
    setPlayCase(key);
    setPlayIndex(at);
    setView("play");
    window.setTimeout(
      () => document.getElementById("qa-playback")?.scrollIntoView({ behavior: "smooth", block: "start" }),
      0,
    );
  };

  return (
    <Page
      title={t("qa.report.title")}
      actions={
        <>
          {run?.batch_id && (
            <Link
              to={`/qa/batches/${run.batch_id}`}
              className="text-[13px] text-primary hover:underline"
            >
              {t("qa.report.back")}
            </Link>
          )}
          <Button
            variant="outline"
            size="sm"
            className="rounded-lg"
            onClick={() => rid && load(rid)}
            disabled={state === "loading"}
          >
            <RefreshCw className="size-4" />
            {t("qa.common.refresh")}
          </Button>
        </>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12px] text-muted-foreground">
        {run && <StatusBadge status={run.status} />}
        {run?.environment_name && <span>{run.environment_name}</span>}
        {run?.model_name && <span className="truncate">{run.model_name}</span>}
        {run?.started_at && <span className="tabular-nums">{stamp(run.started_at)}</span>}
        {run?.duration_ms != null && <span className="tabular-nums">{dur(run.duration_ms)}</span>}
        <span className="font-mono text-[11px] text-muted-foreground/70">
          {rid?.slice(0, 8)}
        </span>
      </div>

      {state === "loading" && (
        <div className="rounded-xl border border-border bg-card px-4 py-10 text-center text-[13px] text-muted-foreground">
          {t("qa.report.loading")}
        </div>
      )}

      {state === "error" && (
        <div className="rounded-xl border border-danger/25 bg-danger/5 px-4 py-6">
          <div className="text-[13px] font-medium text-danger">{t("qa.report.loadFailed")}</div>
          <div className="mt-1 text-[12px] text-muted-foreground">{error}</div>
        </div>
      )}

      {state === "empty" && (
        <div className="rounded-xl border border-border bg-card px-4 py-10 text-center">
          {run && ACTIVE.has(run.status) ? (
            <div className="text-[13px] text-muted-foreground">{t("qa.report.running")}</div>
          ) : (
            <>
              <div className="text-[13px] font-medium text-foreground">{t("qa.report.noData")}</div>
              <div className="mt-1 text-[12px] text-muted-foreground">{t("qa.report.noDataHint")}</div>
              {run?.report_available && (
                <Button variant="outline" size="sm" className="mt-3 rounded-lg" onClick={openOriginal}>
                  {t("qa.report.original")}
                </Button>
              )}
            </>
          )}
        </div>
      )}

      {state === "ready" && report && (
        <>
          <Summary stats={report.stats} />

          <div
            role="tablist"
            aria-label={t("qa.report.title")}
            className="mb-3 inline-flex rounded-lg border border-border bg-card p-0.5"
          >
            {(
              [
                ["play", "qa.report.viewPlay"],
                ["detail", "qa.report.viewDetail"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={view === key}
                onClick={() => setView(key)}
                className={`rounded-md px-3 py-1 text-[12px] transition-colors ${
                  view === key
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {t(label)}
              </button>
            ))}
          </div>

          {view === "play" ? (
            <>
              {cases.length > 1 && (
                <nav className="mb-3 flex flex-wrap gap-1.5">
                  {cases.map(({ item }) => {
                    const key = caseKey(item);
                    const active = selected && caseKey(selected.item) === key;
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => {
                          setPlayCase(key);
                          setPlayIndex(0);
                        }}
                        aria-current={active ? "true" : undefined}
                        className={`inline-flex max-w-64 items-center gap-1.5 truncate rounded-full border px-2.5 py-1 text-[11.5px] transition-colors ${
                          active
                            ? "border-primary/40 bg-primary/10 text-foreground"
                            : "border-border bg-card text-muted-foreground hover:text-foreground"
                        }`}
                        title={clean(item.name)}
                      >
                        <span
                          className={`size-1.5 shrink-0 rounded-full ${
                            DOT[item.status] || "bg-muted-foreground/40"
                          }`}
                        />
                        <span className="truncate">{clean(item.name)}</span>
                      </button>
                    );
                  })}
                </nav>
              )}
              {selected ? (
                <PlaybackCase
                  item={selected.item}
                  rid={rid || ""}
                  index={Math.min(playIndex, Math.max(selectedFrames.length - 1, 0))}
                  onIndex={setPlayIndex}
                  onZoom={(i) => {
                    const frames = selectedFrames.map((f) => f.frame);
                    if (frames[i]) onZoom(frames, i);
                  }}
                />
              ) : (
                <div className="rounded-xl border border-border bg-card px-4 py-10 text-center text-[12.5px] text-muted-foreground">
                  {t("common.noData")}
                </div>
              )}
            </>
          ) : (
            <>
              {cases.length > 1 && (
                <nav className="mb-3 flex flex-wrap gap-1.5">
                  {cases.map(({ item }) => (
                    <button
                      key={caseKey(item)}
                      type="button"
                      onClick={() =>
                        document
                          .getElementById(`case-${caseKey(item)}`)
                          ?.scrollIntoView({ behavior: "smooth", block: "start" })
                      }
                      className="max-w-64 truncate rounded-full border border-border bg-card px-2.5 py-1 text-[11.5px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
                      title={clean(item.name)}
                    >
                      {clean(item.name)}
                    </button>
                  ))}
                </nav>
              )}

              <div className="space-y-4">
                {cases.map(({ item, project }) => (
                  <CaseBlock
                    key={caseKey(item)}
                    item={item}
                    project={project}
                    onSeek={(stepId) => seek(caseKey(item), stepId)}
                  />
                ))}
              </div>
            </>
          )}

          <UsageDetails stats={report.stats} />

          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            <span>{t("qa.report.generatedAt")} {stamp(report.generatedAt)}</span>
            {report.group.sdkVersion && <span>SDK {report.group.sdkVersion}</span>}
            {report.group.deviceType && <span>{report.group.deviceType}</span>}
            {report.legacy && <span className="text-warning">{t("qa.report.legacy")}</span>}
          </div>
        </>
      )}

      {zoom && rid && (
        <Lightbox
          frames={zoom.frames}
          index={zoom.index}
          rid={rid}
          onIndex={(next) => setZoom((prev) => (prev ? { ...prev, index: next } : prev))}
          onClose={closeZoom}
        />
      )}
    </Page>
  );
}