/**
 * 把一次执行（批次）下多个 run 的 HTML 报告合并成一份聚合报告。
 *
 *   node scripts/merge-reports.mjs '<manifestJson>' <outputDir>
 *
 * manifest: [{ runId, projectName, status, startedAt, durationMs, htmlPath }]
 *
 * 产物（outputDir 下）：
 *   batch-report/index.html
 *   batch-report/markdown/report.md
 *   batch-report/markdown/report.json   v2 结构，projects 按 manifest 顺序对齐
 *   batch-report/markdown/screenshots/
 */
import { existsSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { mergeReportFiles, reportFileToMarkdown } from '@midscene/core';
import { buildReport, writeReportJson } from './lib/report-v2.mjs';

const [manifestRaw, outputDir] = process.argv.slice(2);
if (!manifestRaw || !outputDir) {
  console.error('usage: merge-reports.mjs <manifestJson> <outputDir>');
  process.exit(2);
}

let manifest;
try {
  manifest = JSON.parse(manifestRaw);
} catch (error) {
  console.error(`invalid manifest: ${error.message}`);
  process.exit(2);
}
if (!Array.isArray(manifest)) {
  console.error('manifest must be an array');
  process.exit(2);
}

const sources = manifest.filter(
  (item) => item?.htmlPath && existsSync(isAbsolute(item.htmlPath) ? item.htmlPath : resolve(item.htmlPath)),
);
if (sources.length === 0) {
  console.log(JSON.stringify({ merged: 0, reason: 'no report html files' }));
  process.exit(0);
}

const merged = mergeReportFiles({
  htmlPaths: sources.map((item) => resolve(item.htmlPath)),
  outputDir,
  outputName: 'batch-report',
  overwrite: true,
});
const mergedHtml = merged.mergedReportPath;
const markdownDir = join(join(outputDir, 'batch-report'), 'markdown');

const markdown = await reportFileToMarkdown({ htmlPath: mergedHtml, outputDir: markdownDir });
const structured = buildReport(mergedHtml, {
  kind: 'batch',
  sources: sources.map((item) => ({
    runId: item.runId || null,
    projectName: item.projectName || '',
    status: item.status || '',
    startedAt: item.startedAt || null,
    durationMs: item.durationMs ?? null,
    shardIndex: item.shardIndex ?? null,
    shardCount: item.shardCount ?? null,
  })),
  markdownDir,
});
writeReportJson(markdownDir, structured);

console.log(
  JSON.stringify({
    merged: sources.length,
    html: mergedHtml,
    markdown: markdown.markdownFiles?.[0] || null,
    structured: join(markdownDir, 'report.json'),
    projects: structured.stats.projects,
    cases: structured.stats.cases,
    steps: structured.stats.steps,
    keyframes: structured.stats.keyframes,
  }),
);
