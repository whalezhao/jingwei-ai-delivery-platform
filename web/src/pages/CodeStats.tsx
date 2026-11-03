import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Button, Empty, Segmented, Select, Tooltip, message } from "antd";
import { useTranslation } from "react-i18next";
import ResizableTable from "../ResizableTable";
import { api, isAdmin } from "../api";
import { PAGE } from "../table";

/**
 * 工作台 · 代码统计：读每日落库的 DailyStat / FileStat，按区间聚合。
 * 与日报/周报/月报同一份数据源，所以页面数字和群里推送的一致。
 * 默认今天；「拉取数据」按当前区间现采（看当天、补历史都靠它）。
 */

interface Row {
  commits: number;
  additions: number;
  deletions: number;
}

interface Day extends Row {
  date: string;
}

interface Stats {
  start: string;
  end: string;
  totals: Row & { people: number; projects: number };
  people: (Row & { person_id: number | null; name: string; matched: boolean })[];
  projects: (Row & { project_id: number; path: string })[];
  hot_files: (Row & { project_id: number; project: string; path: string })[];
  daily: Day[];
}

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function range(kind: string): [string, string] {
  const today = new Date();
  const back = (n: number) => {
    const d = new Date(today);
    d.setDate(d.getDate() - n);
    return d;
  };
  if (kind === "yesterday") return [iso(back(1)), iso(back(1))];
  if (kind === "week") return [iso(back(6)), iso(today)];
  if (kind === "month") return [iso(back(29)), iso(today)];
  return [iso(today), iso(today)];
}

/** 区间里没有提交的日子也要画出来，否则趋势图的 x 轴会撒谎。 */
function fillDays(start: string, end: string, daily: Day[]): Day[] {
  const byDate = new Map(daily.map((d) => [d.date, d]));
  const out: Day[] = [];
  const d = new Date(`${start}T00:00:00`);
  const last = new Date(`${end}T00:00:00`);
  while (d <= last && out.length < 62) {
    const key = iso(d);
    out.push(byDate.get(key) || { date: key, commits: 0, additions: 0, deletions: 0 });
    d.setDate(d.getDate() + 1);
  }
  return out;
}

/** 趋势线：一条序列一个色，10% 面积衬底 + 2px 线；悬停出十字线和数值。 */
function TrendChart({ days }: { days: Day[] }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(880);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // 页签刚激活时容器可能还量不到宽（0），补一帧 + 定时再量，否则会停在默认 880 只占半边
    const sync = () => el.clientWidth > 0 && setW(el.clientWidth);
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    const raf = requestAnimationFrame(sync);
    const timer = setTimeout(sync, 300);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(raf);
      clearTimeout(timer);
    };
  }, []);

  const H = 200;
  const L = 38;
  const R = 20;
  const T = 14;
  const B = 26;
  const iw = Math.max(60, w - L - R);
  const ih = H - T - B;
  const max = Math.max(1, ...days.map((d) => d.commits));
  const stepX = days.length > 1 ? iw / (days.length - 1) : 0;
  const x = (i: number) => L + (days.length > 1 ? i * stepX : iw / 2);
  const y = (v: number) => T + ih - (v / max) * ih;
  const path = days.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.commits).toFixed(1)}`).join(" ");
  const last = days[days.length - 1];
  const ticks = [...new Set([max, Math.round(max / 2), 0])]; // max=1 时 1 和 round(.5) 重复，key 撞了会残留节点
  const labelEvery = Math.max(1, Math.ceil((days.length - 1) / 4));
  const hd = hover != null ? days[hover] : null;

  return (
    // 容器必须常驻：数据没到时若整块换成 Empty，ref 挂不上，测量用的 effect 再也不会跑
    <div className="cs-trend" ref={ref}>
      {!days.length ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("codeStats.emptyRange")} />
      ) : (
        <>
      <svg
        width={w}
        height={H}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const i = Math.round((e.clientX - box.left - L) / (stepX || 1));
          setHover(Math.max(0, Math.min(days.length - 1, i)));
        }}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((v, i) => (
          <g key={i}>
            <line className="cs-grid-line" x1={L} x2={L + iw} y1={y(v)} y2={y(v)} />
            <text className="cs-axis" x={L - 8} y={y(v) + 4} textAnchor="end">
              {v}
            </text>
          </g>
        ))}
        {days.map((d, i) =>
          i % labelEvery === 0 || i === days.length - 1 ? (
            <text key={d.date} className="cs-axis" x={x(i)} y={H - 8} textAnchor="middle">
              {d.date.slice(5)}
            </text>
          ) : null
        )}
        <path className="cs-area" d={`${path} L${x(days.length - 1)},${y(0)} L${x(0)},${y(0)} Z`} />
        <path className="cs-line" d={path} />
        {hd && hover != null && (
          <line className="cs-cross" x1={x(hover)} x2={x(hover)} y1={T} y2={T + ih} />
        )}
        <circle className="cs-dot" cx={x(days.length - 1)} cy={y(last.commits)} r={4} />
        <text
          className="cs-axis"
          x={x(days.length - 1) - 8}
          y={y(last.commits) - 10}
          textAnchor="end"
          style={{ fill: "var(--ink-2)", fontWeight: 600 }}
        >
          {last.commits}
        </text>
        {hd && hover != null && <circle className="cs-dot" cx={x(hover)} cy={y(hd.commits)} r={4} />}
      </svg>
      {hd && hover != null && (
        <div
          className="cs-tip"
          style={{
            left: Math.min(Math.max(x(hover), 64), w - 64),
            top: y(hd.commits) + (y(hd.commits) > 76 ? -12 : 18),
            transform: y(hd.commits) > 76 ? undefined : "translate(-50%, 0)",
          }}
        >
          <b>{t("codeStats.commitsCount", { n: hd.commits })}</b>
          <span>
            {hd.date} · +{hd.additions}/-{hd.deletions}
          </span>
        </div>
      )}
        </>
      )}
    </div>
  );
}

const MEDAL = ["🥇", "🥈", "🥉"];

/** 横向排行：长度就是数值，值写在末端，不再另画图例。名字会被截断，悬停给全文。 */
function RankBars({
  items,
  emptyText,
  unit,
  fmt,
  wide,
  medal,
}: {
  items: { label: string; value: number }[];
  emptyText: string;
  unit?: string;
  fmt?: (n: number) => string;
  wide?: boolean;
  medal?: boolean;
}) {
  const { t } = useTranslation();
  const unitText = unit ?? t("codeStats.unitCommits");
  if (!items.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} style={{ padding: "20px 0" }} />;
  }
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className={wide ? "cs-bars cs-bars-wide" : "cs-bars"}>
      {items.map((it, i) => (
        <div className="cs-bar-row" key={it.label}>
          <span className="cs-bar-name">
            <Tooltip title={it.label} placement="topLeft" mouseEnterDelay={0.15}>
              <span>
                {medal && i < 3 && <b className="cs-medal">{MEDAL[i]}</b>}
                {it.label}
              </span>
            </Tooltip>
          </span>
          <span
            className="cs-bar-track"
            title={t("codeStats.barTip", { label: it.label, value: it.value, unit: unitText })}
          >
            <i className="cs-bar-fill" style={{ width: `${(it.value / max) * 100}%` }} />
          </span>
          <span
            className="cs-bar-val"
            title={t("codeStats.barTip", { label: it.label, value: it.value, unit: unitText })}
          >
            {fmt ? fmt(it.value) : it.value}
          </span>
        </div>
      ))}
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="page-card" style={{ marginBottom: 14 }}>
      <div className="cs-sec-head">
        <h4>{title}</h4>
        {hint && <span>{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export default function CodeStats({ groupId, projectId }: { groupId?: number; projectId?: number }) {
  const { t, i18n } = useTranslation();
  const loc = i18n.resolvedLanguage?.startsWith("zh") ? "zh-CN" : "en-US";
  const num = (title: string, key: keyof Row) => ({
    title,
    dataIndex: key,
    align: "right" as const,
    render: (n: number) => n.toLocaleString(loc),
  });
  const [kind, setKind] = useState("today");
  const [personId, setPersonId] = useState<number | undefined>();
  const [people, setPeople] = useState<any[]>([]);
  const [data, setData] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    api<any[]>("/api/people")
      .then(setPeople)
      .catch(() => {});
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const [start, end] = range(kind);
    const p = new URLSearchParams({ start, end });
    if (groupId) p.set("group_id", String(groupId));
    if (projectId) p.set("project_id", String(projectId));
    if (personId) p.set("person_id", String(personId));
    api<Stats>(`/api/code-stats?${p}`)
      .then((d) => alive && setData(d))
      .catch(() => alive && setData(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [kind, groupId, projectId, personId, tick]);

  const pull = async () => {
    if (!groupId) {
      message.warning(t("codeStats.chooseGroupFirst"));
      return;
    }
    const [start, end] = range(kind);
    const p = new URLSearchParams({ group_id: String(groupId), start, end });
    if (projectId) p.set("project_id", String(projectId));
    setPulling(true);
    try {
      const d = await api<any>(`/api/code-stats/collect?${p}`, { method: "POST" });
      message.success(t("codeStats.collected", { days: d.days, rows: d.rows, files: d.files }));
      setTick((t) => t + 1);
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setPulling(false);
    }
  };

  const totals = data?.totals;
  const days = useMemo(
    () => (data ? fillDays(data.start, data.end, data.daily) : []),
    [data]
  );
  const peak = useMemo(() => Math.max(0, ...days.map((d) => d.commits)), [days]);
  const hotMax = useMemo(
    () => Math.max(1, ...(data?.hot_files || []).map((f) => f.commits)),
    [data]
  );

  const cards: [string, string, string][] = personId
    ? [
        [t("codeStats.kpiCommits"), totals ? totals.commits.toLocaleString(loc) : "-", "live"],
        [t("codeStats.kpiAdditions"), totals ? `+${totals.additions.toLocaleString(loc)}` : "-", "ok"],
        [t("codeStats.kpiDeletions"), totals ? `-${totals.deletions.toLocaleString(loc)}` : "-", "bad"],
        [t("codeStats.kpiProjects"), totals ? String(totals.projects) : "-", ""],
      ]
    : [
        [t("codeStats.kpiCommits"), totals ? totals.commits.toLocaleString(loc) : "-", "live"],
        [t("codeStats.kpiAdditions"), totals ? `+${totals.additions.toLocaleString(loc)}` : "-", "ok"],
        [t("codeStats.kpiDeletions"), totals ? `-${totals.deletions.toLocaleString(loc)}` : "-", "bad"],
        [t("codeStats.kpiPeople"), totals ? String(totals.people) : "-", ""],
        [t("codeStats.kpiProjectCount"), totals ? String(totals.projects) : "-", ""],
      ];

  return (
    <div>
      <div className="wb-bar">
        <Segmented
          value={kind}
          onChange={(v) => setKind(v as string)}
          options={[
            { value: "today", label: t("codeStats.rangeToday") },
            { value: "yesterday", label: t("codeStats.rangeYesterday") },
            { value: "week", label: t("codeStats.rangeWeek") },
            { value: "month", label: t("codeStats.rangeMonth") },
          ]}
        />
        <Select
          style={{ width: 180 }}
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder={t("codeStats.personPlaceholder")}
          value={personId}
          onChange={setPersonId}
          options={people.map((p) => ({ value: p.id, label: p.name }))}
        />
        {isAdmin() && (
          <Button loading={pulling} onClick={pull}>
            {t("codeStats.pull")}
          </Button>
        )}
        {data && (
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            {data.start} ~ {data.end}
            {data.totals.commits === 0 && t("codeStats.noDataHint")}
          </span>
        )}
      </div>

      <div className="wb-stats" style={{ gridTemplateColumns: `repeat(${cards.length}, 1fr)` }}>
        {cards.map(([label, value, tone]) => (
          <div className={`wb-stat ${tone}`} key={label}>
            <b>{value}</b>
            <span>{label}</span>
          </div>
        ))}
      </div>

      <Section
        title={t("codeStats.trend")}
        hint={t("codeStats.trendHint", { start: data?.start || "", end: data?.end || "", peak })}
      >
        <TrendChart days={days} />
      </Section>

      {personId ? (
        <Section
          title={t("codeStats.personProjects")}
          hint={t("codeStats.projectCountHint", { n: data?.projects.length || 0 })}
        >
          <RankBars
            wide
            emptyText={t("codeStats.noCommits")}
            items={(data?.projects || []).map((p) => ({ label: p.path, value: p.commits }))}
          />
        </Section>
      ) : (
        <>
          <div className="cs-grid2">
            <Section title={t("codeStats.rank")} hint={t("codeStats.rankHint")}>
              <RankBars
                medal
                emptyText={t("codeStats.noCommits")}
                items={(data?.people || [])
                  .slice(0, 10)
                  .map((p) => ({
                    label: p.matched ? p.name : t("codeStats.unmatched", { name: p.name }),
                    value: p.commits,
                  }))}
              />
            </Section>
            <Section title={t("codeStats.addedRank")} hint={t("codeStats.addedRankHint")}>
              <RankBars
                medal
                emptyText={t("codeStats.noCommits")}
                unit={t("codeStats.unitLines")}
                fmt={(v) => `+${v.toLocaleString(loc)}`}
                items={(data?.people || [])
                  .slice()
                  .sort((a, b) => b.additions - a.additions)
                  .slice(0, 10)
                  .map((p) => ({
                    label: p.matched ? p.name : t("codeStats.unmatched", { name: p.name }),
                    value: p.additions,
                  }))}
              />
            </Section>
          </div>
          <Section
            title={t("codeStats.distribution")}
            hint={t("codeStats.distributionHint", { n: data?.projects.length || 0 })}
          >
            <RankBars
              wide
              medal
              emptyText={t("codeStats.noCommits")}
              items={(data?.projects || []).map((p) => ({ label: p.path, value: p.commits }))}
            />
          </Section>
        </>
      )}

      {!personId && (
        <Section title={t("codeStats.hotFiles")} hint={t("codeStats.hotFilesHint")}>
          <ResizableTable
            size="small"
            rowKey={(r) => `${r.project_id}:${r.path}`}
            loading={loading}
            pagination={PAGE}
            dataSource={data?.hot_files || []}
            columns={[
              {
                title: t("codeStats.colFile"),
                dataIndex: "path",
                ellipsis: { showTitle: false },
                render: (v: string) => (
                  <Tooltip title={v} placement="topLeft" mouseEnterDelay={0.15}>
                    <span>{v}</span>
                  </Tooltip>
                ),
              },
              {
                title: t("codeStats.colProject"),
                dataIndex: "project",
                ellipsis: { showTitle: false },
                render: (v: string) => (
                  <Tooltip title={v} placement="topLeft" mouseEnterDelay={0.15}>
                    <span>{v}</span>
                  </Tooltip>
                ),
              },
              {
                title: t("codeStats.colChanges"),
                dataIndex: "commits",
                width: 150,
                render: (n: number) => (
                  <span className="cs-inline">
                    <span className="cs-bar-track">
                      <i className="cs-bar-fill" style={{ width: `${(n / hotMax) * 100}%` }} />
                    </span>
                    {n}
                  </span>
                ),
              },
              num(t("codeStats.colAdditions"), "additions"),
              num(t("codeStats.colDeletions"), "deletions"),
            ]}
          />
        </Section>
      )}

      <Section title={t("codeStats.dailyDetail")} hint={t("codeStats.dailyHint")}>
        <ResizableTable
          size="small"
          rowKey="date"
          loading={loading}
          pagination={PAGE}
          dataSource={data?.daily || []}
          columns={[
            { title: t("codeStats.colDate"), dataIndex: "date" },
            num(t("codeStats.colCommits"), "commits"),
            num(t("codeStats.colAdditions"), "additions"),
            num(t("codeStats.colDeletions"), "deletions"),
          ]}
        />
      </Section>
    </div>
  );
}
