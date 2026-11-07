/**
 * 把 midscene 的 HTML 报告转成平台可渲染的产物：
 *
 *   node scripts/report-to-markdown.mjs <htmlPath> <outputDir>
 *
 * 输出目录里会得到：
 *   - report.md          Markdown 版本（旧前端 / 纯文本消费）
 *   - report.json        v2 结构化版本：用例 → 步骤 → 关键帧 / 执行明细
 *   - screenshots/*.jpeg 截图
 *
 * 平台在每次执行收尾时调用它；失败不影响执行结果。
 */
import { join } from 'node:path';
import { reportFileToMarkdown } from '@midscene/core';
import { buildReport, writeReportJson } from './lib/report-v2.mjs';

const [htmlPath, outputDir] = process.argv.slice(2);
if (!htmlPath || !outputDir) {
  console.error('usage: report-to-markdown.mjs <htmlPath> <outputDir>');
  process.exit(2);
}

const result = await reportFileToMarkdown({ htmlPath, outputDir });
const markdownFiles = result.markdownFiles ?? [];
if (markdownFiles.length === 0) {
  console.error('no markdown generated');
  process.exit(3);
}

const structured = buildReport(htmlPath, { kind: 'run', markdownDir: outputDir });
writeReportJson(outputDir, structured);

console.log(
  JSON.stringify({
    markdown: markdownFiles[0],
    screenshots: (result.screenshotFiles ?? []).length,
    structured: join(outputDir, 'report.json'),
    cases: structured.stats.cases,
    steps: structured.stats.steps,
    keyframes: structured.stats.keyframes,
  }),
);
