import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Button, Empty, Segmented, Select, Tooltip, message } from "antd";
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
    const t = setTimeout(sync, 300);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(raf);
      clearTimeout(t);
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
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该区间没有数据" />
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
        {ticks.map((t, i) => (
          <g key={i}>
            <line className="cs-grid-line" x1={L} x2={L + iw} y1={y(t)} y2={y(t)} />
            <text className="cs-axis" x={L - 8} y={y(t) + 4} textAnchor="end">
              {t}
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
          <b>{hd.commits} 次提交</b>
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
  unit = "次提交",
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
          <span className="cs-bar-track" title={`${it.label}：${it.value} ${unit}`}>
            <i className="cs-bar-fill" style={{ width: `${(it.value / max) * 100}%` }} />
          </span>
          <span className="cs-bar-val" title={`${it.label}：${it.value} ${unit}`}>
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

const num = (title: string, key: keyof Row) => ({
  title,
  dataIndex: key,
  align: "right" as const,
  render: (n: number) => n.toLocaleString(),
});

export default function CodeStats({ groupId, projectId }: { groupId?: number; projectId?: number }) {
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
      message.warning("先在上方选择研发组");
      return;
    }
    const [start, end] = range(kind);
    const p = new URLSearchParams({ group_id: String(groupId), start, end });
    if (projectId) p.set("project_id", String(projectId));
    setPulling(true);
    try {
      const d = await api<any>(`/api/code-stats/collect?${p}`, { method: "POST" });
      message.success(`已采集 ${d.days} 天：${d.rows} 条人员记录、${d.files} 个文件`);
      setTick((t) => t + 1);
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setPulling(false);
    }
  };

  const t = data?.totals;
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
        ["提交", t ? t.commits.toLocaleString() : "-", "live"],
        ["新增行", t ? `+${t.additions.toLocaleString()}` : "-", "ok"],
        ["删除行", t ? `-${t.deletions.toLocaleString()}` : "-", "bad"],
        ["参与项目", t ? String(t.projects) : "-", ""],
      ]
    : [
        ["提交", t ? t.commits.toLocaleString() : "-", "live"],
        ["新增行", t ? `+${t.additions.toLocaleString()}` : "-", "ok"],
        ["删除行", t ? `-${t.deletions.toLocaleString()}` : "-", "bad"],
        ["参与人数", t ? String(t.people) : "-", ""],
        ["项目数", t ? String(t.projects) : "-", ""],
      ];

  return (
    <div>
      <div className="wb-bar">
        <Segmented
          value={kind}
          onChange={(v) => setKind(v as string)}
          options={[
            { value: "today", label: "今天" },
            { value: "yesterday", label: "昨天" },
            { value: "week", label: "最近一周" },
            { value: "month", label: "最近一月" },
          ]}
        />
        <Select
          style={{ width: 180 }}
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="视角：团队汇总"
          value={personId}
          onChange={setPersonId}
          options={people.map((p) => ({ value: p.id, label: p.name }))}
        />
        {isAdmin() && (
          <Button loading={pulling} onClick={pull}>
            拉取数据
          </Button>
        )}
        {data && (
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            {data.start} ~ {data.end}
            {data.totals.commits === 0 && " · 该区间暂无数据，可选组后点「拉取数据」"}
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

      <Section title="📈 提交趋势" hint={`${data?.start || ""} ~ ${data?.end || ""} · 单日峰值 ${peak}`}>
        <TrendChart days={days} />
      </Section>

      {personId ? (
        <Section title="📦 参与项目" hint={`${data?.projects.length || 0} 个`}>
          <RankBars
            wide
            emptyText="该区间没有提交"
            items={(data?.projects || []).map((p) => ({ label: p.path, value: p.commits }))}
          />
        </Section>
      ) : (
        <>
          <div className="cs-grid2">
            <Section title="🏆 提交排行" hint="按提交次数">
              <RankBars
                medal
                emptyText="该区间没有提交"
                items={(data?.people || [])
                  .slice(0, 10)
                  .map((p) => ({ label: p.matched ? p.name : `${p.name}（未匹配）`, value: p.commits }))}
              />
            </Section>
            <Section title="➕ 新增行排行" hint="按新增代码行数">
              <RankBars
                medal
                emptyText="该区间没有提交"
                unit="行新增"
                fmt={(v) => `+${v.toLocaleString()}`}
                items={(data?.people || [])
                  .slice()
                  .sort((a, b) => b.additions - a.additions)
                  .slice(0, 10)
                  .map((p) => ({ label: p.matched ? p.name : `${p.name}（未匹配）`, value: p.additions }))}
              />
            </Section>
          </div>
          <Section title="📦 项目分布" hint={`${data?.projects.length || 0} 个项目`}>
            <RankBars
              wide
              medal
              emptyText="该区间没有提交"
              items={(data?.projects || []).map((p) => ({ label: p.path, value: p.commits }))}
            />
          </Section>
        </>
      )}

      {!personId && (
        <Section title="🔥 热点文件" hint="区间内改动次数最多的文件">
          <ResizableTable
            size="small"
            rowKey={(r) => `${r.project_id}:${r.path}`}
            loading={loading}
            pagination={PAGE}
            dataSource={data?.hot_files || []}
            columns={[
              {
                title: "文件",
                dataIndex: "path",
                ellipsis: { showTitle: false },
                render: (v: string) => (
                  <Tooltip title={v} placement="topLeft" mouseEnterDelay={0.15}>
                    <span>{v}</span>
                  </Tooltip>
                ),
              },
              {
                title: "项目",
                dataIndex: "project",
                ellipsis: { showTitle: false },
                render: (v: string) => (
                  <Tooltip title={v} placement="topLeft" mouseEnterDelay={0.15}>
                    <span>{v}</span>
                  </Tooltip>
                ),
              },
              {
                title: "改动次数",
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
              num("新增", "additions"),
              num("删除", "deletions"),
            ]}
          />
        </Section>
      )}

      <Section title="📅 按日明细" hint="图上的每个点都在这张表里">
        <ResizableTable
          size="small"
          rowKey="date"
          loading={loading}
          pagination={PAGE}
          dataSource={data?.daily || []}
          columns={[
            { title: "日期", dataIndex: "date" },
            num("提交", "commits"),
            num("新增", "additions"),
            num("删除", "deletions"),
          ]}
        />
      </Section>
    </div>
  );
}
