"use client";

import { useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { FeedbackTarget } from "@/lib/api";
import { useChartColors } from "@/lib/theme";
import type { Chart, ChartSpec, DataChartSpec, NewsTimelineSpec, TableSpec } from "@/lib/checkState";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtMonth = (m: string) => `${MONTHS[Number(m.slice(5, 7)) - 1]} ’${m.slice(2, 4)}`;

/** Bucket keys sort as strings: YYYY, YYYY-Qn, YYYY-MM or the Monday of a week (YYYY-MM-DD). */
function fmtBucket(b: string, interval: DataChartSpec["interval"]) {
  if (interval === "year") return b;
  if (interval === "quarter") return `${b.slice(5)} ’${b.slice(2, 4)}`;
  if (interval === "week") return `${Number(b.slice(8))} ${MONTHS[Number(b.slice(5, 7)) - 1]} ’${b.slice(2, 4)}`;
  return fmtMonth(b);
}

function fmtValue(v: unknown, unit: DataChartSpec["y"]["unit"]) {
  if (typeof v !== "number") return "—";
  if (unit === "rating") return v.toFixed(2);
  if (unit === "percent") return `${v}%`;
  return String(v);
}

function ChartTooltip({
  active,
  payload,
  spec,
}: {
  active?: boolean;
  payload?: { payload: Record<string, number | string | null> }[];
  spec: DataChartSpec;
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="rounded-md border border-line bg-card px-2.5 py-1.5 text-xs shadow-sm">
      <div className="font-medium text-ink">{fmtBucket(String(row.bucket), spec.interval)}</div>
      {spec.series.map((s) => {
        const n = row[`${s.key}_n`];
        return (
          <div key={s.key} className="text-muted">
            {s.label}{" "}
            <span className="font-medium tabular-nums text-ink">{fmtValue(row[s.key], spec.y.unit)}</span>
            {typeof row[s.key] === "number" && s.metric !== "count" && ` · ${n} item${n === 1 ? "" : "s"}`}
          </div>
        );
      })}
    </div>
  );
}

function DataChart({ spec }: { spec: DataChartSpec }) {
  const [showTable, setShowTable] = useState(false);
  const C = useChartColors();
  if (spec.data.length === 0) return <p className="text-sm text-muted">Nothing matched this chart.</p>;
  const colors = [C.series, C.series2, C.ink, C.muted];
  const color = (i: number) => colors[i % colors.length];
  const common = {
    data: spec.data,
    margin: { top: 20, right: 16, bottom: 0, left: -16 },
  };
  const axes = [
    <CartesianGrid key="grid" stroke={C.grid} strokeWidth={1} vertical={false} />,
    <XAxis
      key="x"
      dataKey="bucket"
      tickFormatter={(b: string) => fmtBucket(b, spec.interval)}
      tick={{ fontSize: 11, fill: C.axis }}
      tickLine={false}
      axisLine={{ stroke: C.grid }}
      minTickGap={16}
    />,
    <YAxis
      key="y"
      domain={spec.y.domain ?? [0, "auto"]}
      ticks={spec.y.unit === "rating" ? [1, 2, 3, 4, 5] : undefined}
      allowDecimals={spec.y.unit !== "count"}
      tick={{ fontSize: 11, fill: C.axis }}
      tickLine={false}
      axisLine={false}
    />,
    <Tooltip key="tip" content={<ChartTooltip spec={spec} />} cursor={{ stroke: C.axis, strokeWidth: 1, fill: C.grid, fillOpacity: 0.4 }} />,
    ...(spec.series.length > 1 ? [<Legend key="legend" iconType="plainline" wrapperStyle={{ fontSize: 12 }} />] : []),
    ...spec.markers.map((m) => (
      <ReferenceLine
        key={`${m.date}-${m.label}`}
        x={m.bucket}
        stroke={C.muted}
        strokeWidth={1}
        strokeDasharray="3 3"
        label={{ value: m.label, position: "top", fontSize: 11, fill: C.muted }}
      />
    )),
  ];
  const dash = (i: number) => (i >= 2 ? "5 3" : undefined);
  return (
    <div>
      <div className="h-60 w-full">
        <ResponsiveContainer width="100%" height="100%">
          {spec.kind === "bar" ? (
            <BarChart {...common}>
              {axes}
              {spec.series.map((s, i) => (
                <Bar key={s.key} dataKey={s.key} name={s.label} fill={color(i)} radius={[2, 2, 0, 0]} isAnimationActive={false} />
              ))}
            </BarChart>
          ) : spec.kind === "area" ? (
            <AreaChart {...common}>
              {axes}
              {spec.series.map((s, i) => (
                <Area
                  key={s.key}
                  type="linear"
                  dataKey={s.key}
                  name={s.label}
                  stroke={color(i)}
                  fill={color(i)}
                  fillOpacity={0.18}
                  strokeWidth={2}
                  strokeDasharray={dash(i)}
                  connectNulls
                  isAnimationActive={false}
                />
              ))}
            </AreaChart>
          ) : (
            <LineChart {...common}>
              {axes}
              {spec.series.map((s, i) => (
                <Line
                  key={s.key}
                  type="linear"
                  dataKey={s.key}
                  name={s.label}
                  stroke={color(i)}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  strokeDasharray={dash(i)}
                  connectNulls
                  dot={{ r: 4, fill: color(i), stroke: C.surface, strokeWidth: 2 }}
                  activeDot={{ r: 6, fill: color(i), stroke: C.surface, strokeWidth: 2 }}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
      <div className="mt-1 flex items-center justify-between gap-3 text-xs text-muted">
        <span>
          {spec.y.label} · {fmtBucket(spec.range.start ?? "", spec.interval)} – {fmtBucket(spec.range.end ?? "", spec.interval)} ·{" "}
          {spec.series.map((s) => `${s.label}: ${s.total}`).join(" · ")}
        </span>
        <button onClick={() => setShowTable((v) => !v)} className="shrink-0 hover:text-ink">
          {showTable ? "Hide data" : "Show data"}
        </button>
      </div>
      {showTable && (
        <DataTable
          spec={{
            type: "table",
            columns: [
              { key: "bucket", label: spec.interval[0].toUpperCase() + spec.interval.slice(1) },
              ...spec.series.map((s) => ({ key: s.key, label: s.label, align: "right" as const })),
            ],
            rows: spec.data,
          }}
        />
      )}
    </div>
  );
}

function NewsTimeline({ spec }: { spec: NewsTimelineSpec }) {
  if (spec.events.length === 0) return <p className="text-sm text-muted">No news items.</p>;
  return (
    <div>
      <ol className="relative ml-1.5 border-l border-line">
        {spec.events.map((e) => (
          <li key={e.id} className="relative pb-3 pl-4 last:pb-0">
            <span className="absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full bg-accent ring-2 ring-card" />
            <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
              <time className="font-mono tabular-nums">{e.date}</time>
              <code className="font-mono text-[11px] opacity-70">{e.id}</code>
            </div>
            <div className="text-sm font-medium text-ink">{e.headline}</div>
            <div className="text-sm text-muted">{e.text}</div>
          </li>
        ))}
      </ol>
      {spec.excluded.length > 0 && (
        <p className="mt-2 text-xs text-muted">Left out (excluded evidence): {spec.excluded.join(", ")}</p>
      )}
    </div>
  );
}

function DataTable({ spec }: { spec: TableSpec }) {
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left font-mono text-[11px] uppercase tracking-wide text-muted">
            {spec.columns.map((c) => (
              <th key={c.key} className={`px-2 py-1.5 font-medium ${c.align === "right" ? "text-right" : ""}`}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {spec.rows.map((row, i) => (
            <tr key={i} className="border-b border-line/60 last:border-0">
              {spec.columns.map((c) => (
                <td
                  key={c.key}
                  className={`px-2 py-1.5 text-ink ${c.align === "right" ? "text-right font-mono tabular-nums" : ""}`}
                >
                  {row[c.key] === null || row[c.key] === undefined ? "—" : String(row[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Body({ spec }: { spec: ChartSpec }) {
  switch (spec.type) {
    case "chart":
      return <DataChart spec={spec} />;
    case "news_timeline":
      return <NewsTimeline spec={spec} />;
    case "table":
      return <DataTable spec={spec} />;
  }
}

/** One version of a chart: inline in the activity stream where the agent made it, and the
 * latest version in the case file. */
export function ChartBlock({
  chart,
  onTarget,
  note,
}: {
  chart: Chart;
  onTarget?: (t: FeedbackTarget) => void;
  note?: React.ReactNode;
}) {
  return (
    <section className="rounded-md border border-line bg-card p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium text-ink">{chart.title}</h3>
        {chart.version > 1 && (
          <span className="rounded bg-accent/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase text-accent">
            v{chart.version}
          </span>
        )}
        <code className="ml-auto font-mono text-[11px] text-muted">{chart.chart_id}</code>
        {onTarget && (
          <button
            onClick={() => onTarget({ kind: "chart", id: chart.chart_id })}
            className="text-[11px] text-muted hover:text-accent"
          >
            Feedback
          </button>
        )}
      </div>
      {note && <div className="mt-1 text-[11px] text-muted">{note}</div>}
      <div className="mt-3">
        <Body spec={chart.spec} />
      </div>
    </section>
  );
}
