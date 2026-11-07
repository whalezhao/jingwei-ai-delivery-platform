/**
 * 报告解析的共享实现：把 midscene 的 HTML 报告解析成平台自己的 v2 结构。
 *
 * 为什么不直接沿用 @midscene/core 的 reportToMarkdown：那份 Markdown 是按
 * 「execution 段」平铺的，一个用例会被拆成 Act / Assert 好几段，平台展示时
 * 看不出「这是同一个用例」。v2 以用例为第一层，再从 case.step.agentDetails
 * 映射到 execution，这样关键帧、耗时、断言结果都归到用例下面。
 */
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';import { collectDedupedExecutions, deriveTaskStatus } from '@midscene/core';

const MAX_STRING = 6000;
const SCREENSHOT_ID = /-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.(?:jpeg|jpg|png|webp)$/i;

const TASK_LABELS = {
  Planning: '规划',
  Plan: '规划',
  Locate: '定位',
  'Action Space': '操作',
  Tap: '点击',
  Input: '输入',
  Scroll: '滚动',
  KeyboardPress: '按键',
  DragAndDrop: '拖拽',
  PullGesture: '下拉',
  Sleep: '等待',
  Insight: '断言',
  Assert: '断言',
  WaitFor: '等待条件',
  Query: '查询',
};

const NODE_LABELS = {
  aiAct: '操作',
  aiAssert: '断言',
  aiWaitFor: '等待条件',
  aiQuery: '查询',
  aiInput: '输入',
  aiTap: '点击',
  gotoUrl: '打开页面',
  sleep: '等待',
  'runner:host-result': '执行异常',
  execution: '执行段',
};

export function sanitize(value, depth = 0, seen = new WeakSet()) {
  if (typeof value === 'string') {
    if (/^data:image\/|^[A-Za-z0-9+/=\s]{100000,}$/.test(value)) return '[省略图片数据]';
    return value.length > MAX_STRING
      ? `${value.slice(0, MAX_STRING)}\n[已截断，原文 ${value.length} 字符]`
      : value;
  }
  if (value === null || typeof value !== 'object') return value;
  if (depth > 10) return '[层级过深]';
  if (seen.has(value)) return '[循环引用]';
  seen.add(value);
  if (Array.isArray(value)) return value.map((item) => sanitize(item, depth + 1, seen));
  const output = {};
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === 'function' || entry === undefined) continue;
    output[key] = sanitize(entry, depth + 1, seen);
  }
  seen.delete(value);
  return output;
}

/** 从 HTML 里找出指定 type 的 dump script。JS bundle 里也会出现同样的字符串，所以逐个 JSON.parse 过滤。 */
function parseScripts(htmlPath, scriptType) {
  const html = readFileSync(htmlPath, 'utf-8');
  const pattern = new RegExp(
    `<script[^>]*type="${scriptType}"[^>]*>([\\s\\S]*?)<\\/script>`,
    'gi',
  );
  const results = [];
  let match;
  while ((match = pattern.exec(html)) !== null) {
    try {
      const parsed = JSON.parse(match[1]);
      if (parsed && typeof parsed === 'object') results.push(parsed);
    } catch {
      // JS 模板字符串里的同名片段，跳过
    }
  }
  return results;
}

export function parseTestRunDump(htmlPath) {
  const dumps = parseScripts(htmlPath, 'midscene_test_run_dump');
  return dumps.find((item) => item.kind === 'test-runner' && Array.isArray(item.projects)) ?? null;
}

function locateText(locate) {
  if (!locate) return '';
  if (typeof locate === 'string') return locate;
  if (typeof locate !== 'object') return '';
  if (typeof locate.promptDisplay === 'string') return locate.promptDisplay;
  if (typeof locate.prompt === 'string') return locate.prompt;
  if (locate.prompt && typeof locate.prompt.prompt === 'string') return locate.prompt.prompt;
  if (typeof locate.description === 'string') return locate.description;
  return '';
}

function taskDetail(task) {
  const param = task.param || {};
  if (task.type === 'Planning') {
    if (task.subType === 'Locate') return locateText(param.locate) || locateText(param);
    return (
      task.output?.log ||
      param.userInstructionDisplay ||
      (typeof param.userInstruction === 'string' ? param.userInstruction : '') ||
      ''
    );
  }
  if (task.type === 'Insight') {
    const demand = param.dataDemand ?? param.assertion ?? param.demand;
    if (typeof demand === 'string') return demand;
    if (demand && typeof demand === 'object') return demand.demand || JSON.stringify(demand);
    return '';
  }
  if (task.type === 'Action Space') {
    const locate = locateText(param.locate);
    if (typeof param.value === 'string' || typeof param.value === 'number') {
      return locate ? `${locate} → ${param.value}` : String(param.value);
    }
    if (typeof param.timeMs === 'number') return `${param.timeMs}ms`;
    if (typeof param.scrollType === 'string') return `${param.direction || 'down'}`;
    return locate || '';
  }
  return '';
}

function taskWindow(task) {
  const timing = task.timing || {};
  const start = timing.start ?? timing.callAiStart ?? timing.callActionStart;
  const end =
    timing.end ?? timing.callAiEnd ?? timing.callActionEnd ?? timing.captureAfterCallingSnapshotEnd;
  const cost =
    timing.cost ?? (typeof start === 'number' && typeof end === 'number' ? end - start : null);
  return { start, end, cost };
}

function isoTime(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function errorText(error) {
  if (!error) return null;
  if (typeof error === 'string') return error;
  return error.message || error.name || JSON.stringify(error);
}

/** 截图 id -> markdown/screenshots 下的文件名。报告里的截图引用只带 id，文件名由导出工具生成。 */
function screenshotFileMap(markdownDir, reportDir) {
  const map = new Map();
  const dirs = [join(markdownDir, 'screenshots'), join(reportDir, 'screenshots')];
  for (const dir of dirs) {
    if (!existsSync(dir)) continue;
    for (const name of readdirSync(dir)) {
      const match = name.match(SCREENSHOT_ID);
      if (match && !map.has(match[1])) map.set(match[1], name);
    }
  }
  return map;
}

function foldStats(executions) {
  let minStart = null;
  let maxEnd = null;
  let modelCallTimeMs = 0;
  const models = new Map();
  const addUsage = (usage) => {
    if (!usage) return;
    modelCallTimeMs += Number(usage.time_cost) || 0;
    const name = usage.model_name || usage.response_model_name || 'unknown';
    const row = models.get(name) || {
      model: name,
      description: usage.model_description || '',
      calls: 0,
      promptTokens: 0,
      cachedInput: 0,
      completionTokens: 0,
      totalTokens: 0,
      timeCost: 0,
    };
    row.calls += 1;
    row.promptTokens += Number(usage.prompt_tokens) || 0;
    row.cachedInput += Number(usage.cached_input) || 0;
    row.completionTokens += Number(usage.completion_tokens) || 0;
    row.totalTokens += Number(usage.total_tokens) || 0;
    row.timeCost += Number(usage.time_cost) || 0;
    models.set(name, row);
  };
  for (const execution of executions) {
    for (const task of execution.tasks || []) {
      const { start, end } = taskWindow(task);
      if (typeof start === 'number') minStart = minStart === null ? start : Math.min(minStart, start);
      if (typeof end === 'number') maxEnd = maxEnd === null ? end : Math.max(maxEnd, end);
      addUsage(task.usage);
      addUsage(task.searchAreaUsage);
    }
  }
  const modelRows = Array.from(models.values());
  return {
    start: minStart,
    end: maxEnd,
    durationMs: minStart !== null && maxEnd !== null ? maxEnd - minStart : null,
    modelCallTimeMs,
    tokens: modelRows.reduce(
      (acc, row) => ({
        prompt: acc.prompt + row.promptTokens,
        cached: acc.cached + row.cachedInput,
        completion: acc.completion + row.completionTokens,
        total: acc.total + row.totalTokens,
      }),
      { prompt: 0, cached: 0, completion: 0, total: 0 },
    ),
    models: modelRows,
  };
}

function taskView(task, index) {
  const window = taskWindow(task);
  const status = deriveTaskStatus(task);
  return {
    index: index + 1,
    type: task.type || '',
    subType: task.subType || '',
    title: TASK_LABELS[task.subType] || TASK_LABELS[task.type] || task.subType || task.type || '步骤',
    detail: taskDetail(task),
    status,
    start: window.start ?? null,
    end: window.end ?? null,
    durationMs: typeof window.cost === 'number' ? window.cost : null,
    error: errorText(task.errorMessage || task.error),
    thought: task.thought || '',
    reasoning: task.reasoning_content || '',
    usage: sanitize(task.usage || null),
    param: sanitize(task.param ?? null),
    output: sanitize(task.output ?? null),
    log: sanitize(task.log ?? null),
  };
}

function screenshotLabel(item, kind) {
  if (item?.description) return item.description;
  if (kind === 'ui-context') return '操作前';
  if (item?.timing === 'after-calling') return '操作后';
  return '过程截图';
}

function keyframesFor(executionsRaw, shotMap) {
  const frames = [];
  const seen = new Set();
  for (const execution of executionsRaw) {
    for (const task of execution.tasks || []) {
      const window = taskWindow(task);
      const candidates = [];
      if (task.uiContext?.screenshot) {
        candidates.push({
          screenshot: task.uiContext.screenshot,
          kind: 'ui-context',
          label: screenshotLabel(null, 'ui-context'),
          time: window.start ?? task.uiContext.screenshot.capturedAt,
        });
      }
      for (const item of task.recorder || []) {
        if (item.screenshot) {
          candidates.push({
            screenshot: item.screenshot,
            kind: item.type || 'screenshot',
            label: screenshotLabel(item, item.type),
            time: item.ts ?? item.screenshot.capturedAt,
          });
        }
      }
      for (const candidate of candidates) {
        const id = candidate.screenshot.id;
        const file = shotMap.get(id);
        if (!file || seen.has(id)) continue;
        seen.add(id);
        frames.push({
          id,
          url: `screenshots/${file}`,
          time: candidate.time ?? null,
          label: candidate.label,
          kind: candidate.kind,
        });
      }
    }
  }
  return frames.sort((left, right) => (left.time ?? 0) - (right.time ?? 0));
}

function executionView(execution, index) {
  const tasks = (execution.tasks || []).map(taskView);
  const failed = tasks.some((task) => task.status === 'failed');
  const warning = tasks.some((task) => task.status === 'warning');
  const starts = tasks.map((task) => task.start).filter((value) => typeof value === 'number');
  const ends = tasks.map((task) => task.end).filter((value) => typeof value === 'number');
  return {
    id: execution.id || `execution-${index + 1}`,
    name: execution.name || `执行段 ${index + 1}`,
    status: failed ? 'failed' : warning ? 'warning' : tasks.length ? 'passed' : 'not-run',
    start: starts.length ? Math.min(...starts) : null,
    end: ends.length ? Math.max(...ends) : null,
    durationMs: starts.length && ends.length ? Math.max(...ends) - Math.min(...starts) : null,
    tasks,
  };
}

function stepView(step, { phase, index, shotMap, byId }) {
  const node = step.node || '';
  const title =
    step.title ||
    NODE_LABELS[node] ||
    (node === 'gotoUrl' ? '打开页面' : node || '步骤');
  const executionsRaw = (step.agentDetails || [])
    .map((item) => byId.get(item.executionId))
    .filter(Boolean);
  const executions = executionsRaw.map((execution, executionIndex) =>
    executionView(execution, executionIndex),
  );
  const keyframes = keyframesFor(executionsRaw, shotMap);
  const status =
    step.status === 'success'
      ? 'passed'
      : step.status === 'failed' || step.status === 'timedOut'
        ? 'failed'
        : step.status || 'not-run';
  const output = step.output || {};
  const assertion = output?.data?.value;
  const firstTask = executions[0]?.tasks?.[0];
  const summary = output.summary || firstTask?.thought || '';
  return {
    id: step.id || `${phase}-${index}`,
    index: index + 1,
    phase,
    node,
    title,
    status,
    durationMs: typeof step.durationMs === 'number' ? step.durationMs : null,
    startedAt: isoTime(step.startedAt),
    endedAt: isoTime(step.endedAt),
    summary,
    thought: output.summary ? assertion?.thought || '' : '',
    error: errorText(step.error),
    notRunReason: step.notRunReason || '',
    continuedAfterError: Boolean(step.continuedAfterError),
    synthetic: false,
    keyframes,
    executions,
    param: sanitize(step.input?.value ?? null),
    outputData: sanitize(step.output ?? null),
    metrics: sanitize(step.metrics ?? null),
  };
}

function caseSteps(attempt, planCase, shotMap, byId) {
  if (!attempt) {
    const planSteps = [...(planCase.beforeEach || []), ...(planCase.steps || []), ...(planCase.afterEach || [])];
    return planSteps.map((step, index) =>
      stepView(
        { ...step, status: 'not-run' },
        { phase: 'steps', index, shotMap, byId },
      ),
    );
  }
  const items = [
    ...(attempt.beforeEach || []).map((step) => ({ step, phase: 'beforeEach' })),
    ...(attempt.steps || []).map((step) => ({ step, phase: 'steps' })),
    ...(attempt.afterEach || []).map((step) => ({ step, phase: 'afterEach' })),
  ];
  return items.map(({ step, phase }, index) =>
    stepView(step, { phase, index, shotMap, byId }),
  );
}

function caseStatus(value) {
  if (value === 'success') return 'passed';
  if (value === 'failed' || value === 'timedOut') return 'failed';
  if (value === 'skipped' || value === 'not-run' || value === 'notRun') return 'not-run';
  return value || 'not-run';
}

function sourceInfo(sourcePath) {
  const match = String(sourcePath || '').match(
    /\.runs[\\/]([0-9a-f-]{36})[\\/]cases[\\/]([0-9a-f-]{36})\.yaml$/i,
  );
  return { runId: match?.[1] || null, caseUuid: match?.[2] || null };
}

function projectCases(project, shotMap, byId) {
  const cases = [];
  for (const document of project.documents || []) {
    for (const item of document.cases || []) {
      const attempt = (item.attempts || []).at(-1) || null;
      const steps = caseSteps(attempt, item.plan || item, shotMap, byId);
      const { runId, caseUuid } = sourceInfo(document.sourcePath || item.plan?.sourcePath);
      cases.push({
        id: item.caseId || `${document.documentId}-${cases.length}`,
        name: item.name || item.plan?.name || `用例 ${cases.length + 1}`,
        caseIndex: item.caseIndex ?? cases.length,
        caseUuid,
        runId,
        sourcePath: document.sourcePath || '',
        status: caseStatus(item.status),
        durationMs: attempt?.durationMs ?? null,
        attemptCount: (item.attempts || []).length,
        attempts: (item.attempts || []).map((entry, attemptIndex) => ({
          index: attemptIndex + 1,
          status: caseStatus(entry.status),
          durationMs: entry.durationMs ?? null,
          startedAt: isoTime(entry.startedAt),
          endedAt: isoTime(entry.endedAt),
        })),
        steps,
      });
    }
  }
  return cases;
}

function legacyProject(baseDump, executions, shotMap) {
  const cases = executions.map((execution, index) => {
    const view = executionView(execution, index);
    const frames = keyframesFor([execution], shotMap);
    const steps = [
      {
        id: `legacy-${index}`,
        index: 1,
        phase: 'steps',
        node: 'execution',
        title: execution.name || `执行段 ${index + 1}`,
        status: view.status,
        durationMs: view.durationMs,
        startedAt: null,
        endedAt: null,
        summary: '',
        thought: '',
        error: null,
        notRunReason: '',
        continuedAfterError: false,
        synthetic: true,
        keyframes: frames,
        executions: [view],
        param: null,
        outputData: null,
        metrics: null,
      },
    ];
    return {
      id: execution.id || `legacy-${index}`,
      name: execution.name || `执行段 ${index + 1}`,
      caseIndex: index,
      caseUuid: null,
      runId: null,
      sourcePath: '',
      status: view.status,
      durationMs: view.durationMs,
      attemptCount: 1,
      attempts: [],
      steps,
    };
  });
  return {
    id: 'legacy',
    name: baseDump.groupName || '执行报告',
    projectName: '',
    runId: null,
    status: cases.some((item) => item.status === 'failed') ? 'failed' : 'passed',
    durationMs: null,
    shardIndex: null,
    shardCount: null,
    cases,
  };
}

export function buildReport(htmlPath, options = {}) {
  const { kind = 'run', sources = [] } = options;
  const reportDir = dirname(htmlPath);
  const markdownDir = options.markdownDir || reportDir;
  const shotMap = screenshotFileMap(markdownDir, reportDir);
  const { baseDump, executions } = collectDedupedExecutions(htmlPath);
  const byId = new Map(executions.map((execution) => [execution.id, execution]));
  const testRun = parseTestRunDump(htmlPath);

  let projects = [];
  let legacy = false;
  if (testRun?.projects?.length) {
    // merge 后的 projects 顺序与输入 htmlPaths 一致；数量对不上时不硬贴标签，避免张冠李戴
    const sourceAligned = sources.length === testRun.projects.length;
    projects = testRun.projects.map((project, projectIndex) => {
      const source = sourceAligned ? sources[projectIndex] : {};
      const cases = projectCases(project, shotMap, byId);
      return {
        id: `${source.runId || 'project'}-${projectIndex}`,
        name: project.name || `系统 ${projectIndex + 1}`,
        projectName: source.projectName || '',
        runId: source.runId || cases.find((item) => item.runId)?.runId || null,
        status: project.status === 'success' ? 'passed' : project.status === 'failed' ? 'failed' : project.status || 'passed',
        durationMs: project.lifecycle?.durationMs ?? null,
        shardIndex: source.shardIndex ?? null,
        shardCount: source.shardCount ?? null,
        cases,
      };
    });
    if (projects.every((project) => project.cases.length === 0)) {
      legacy = true;
      projects = [legacyProject(baseDump, executions, shotMap)];
    }
  } else {
    legacy = true;
    projects = [legacyProject(baseDump, executions, shotMap)];
  }

  const cases = projects.flatMap((project) => project.cases);
  const steps = cases.flatMap((item) => item.steps);
  const keyframeCount = steps.reduce((total, step) => total + step.keyframes.length, 0);
  const folded = foldStats(executions);
  const summary = testRun?.summary || {};
  const passed = Number.isFinite(summary.passed)
    ? summary.passed
    : cases.filter((item) => item.status === 'passed').length;
  const failed = Number.isFinite(summary.failed)
    ? summary.failed
    : cases.filter((item) => item.status === 'failed').length;

  return {
    version: 2,
    kind,
    legacy,
    generatedAt: new Date().toISOString(),
    group: {
      name: baseDump.groupName || '执行报告',
      description: baseDump.groupDescription || '',
      sdkVersion: baseDump.sdkVersion || '',
      deviceType: baseDump.deviceType || '',
    },
    stats: {
      projects: projects.length,
      cases: cases.length,
      passed,
      failed,
      notRun: Number.isFinite(summary.notRun) ? summary.notRun : cases.filter((item) => item.status === 'not-run').length,
      steps: steps.length,
      keyframes: keyframeCount,
      start: testRun?.startedAt ? new Date(testRun.startedAt).getTime() : folded.start,
      end: testRun?.endedAt ? new Date(testRun.endedAt).getTime() : folded.end,
      durationMs: Number.isFinite(testRun?.durationMs) ? testRun.durationMs : folded.durationMs,
      modelCallTimeMs: folded.modelCallTimeMs,
      tokens: folded.tokens,
      models: folded.models,
    },
    sources: sources.map((source) => ({
      runId: source.runId || null,
      projectName: source.projectName || '',
      status: source.status || '',
      startedAt: source.startedAt || null,
      durationMs: source.durationMs ?? null,
      shardIndex: source.shardIndex ?? null,
      shardCount: source.shardCount ?? null,
    })),
    projects,
  };
}

export function writeReportJson(dir, data) {
  writeFileSync(join(dir, 'report.json'), JSON.stringify(data), 'utf-8');
}

export { TASK_LABELS, NODE_LABELS, screenshotFileMap };
