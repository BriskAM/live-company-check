"use client";

import { useState } from "react";
import {
  CartesianGrid,
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
import type { Chart, ChartSpec, RatingTrendSpec, NewsTimelineSpec, TableSpec } from "@/lib/checkState";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtMonth = (m: string) => `${MONTHS[Number(m.slice(5, 7)) - 1]} ’${m.slice(2, 4)}`;

function RatingTooltip({ active, payload }: { active?: boolean; payload?: { payload: { month: string; avg: number; count: number } }[] }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-md border border-line bg-card px-2.5 py-1.5 text-xs shadow-sm">
      <div className="font-medium text-ink">{fmtMonth(d.month)}</div>
      <div className="text-muted">
        Avg rating <span className="font-medium tabular-nums text-ink">{d.avg.toFixed(2)}</span>
        {" · "}
        {d.count} review{d.count === 1 ? "" : "s"}
      </div>
    </div>
  );
}

function RatingTrend({ spec }: { spec: RatingTrendSpec }) {
  const [showTable, setShowTable] = useState(false);
  const C = useChartColors();
  if (spec.data.length === 0) return <p className="text-sm text-muted">No reviews in this range.</p>;
  return (
    <div>
      <div className="h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={spec.data} margin={{ top: 20, right: 16, bottom: 0, left: -16 }}>
            <CartesianGrid stroke={C.grid} strokeWidth={1} vertical={false} />
            <XAxis
              dataKey="month"
              tickFormatter={fmtMonth}
              tick={{ fontSize: 11, fill: C.axis }}
              tickLine={false}
              axisLine={{ stroke: C.grid }}
              minTickGap={16}
            />
            <YAxis
              domain={[1, 5]}
              ticks={[1, 2, 3, 4, 5]}
              tick={{ fontSize: 11, fill: C.axis }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip content={<RatingTooltip />} cursor={{ stroke: C.axis, strokeWidth: 1 }} />
            {spec.markers.map((m) => (
              <ReferenceLine
                key={`${m.date}-${m.label}`}
                x={m.month}
                stroke={C.muted}
                strokeWidth={1}
                strokeDasharray="3 3"
                label={{ value: m.label, position: "top", fontSize: 11, fill: C.muted }}
              />
            ))}
            <Line
              type="linear"
              dataKey="avg"
              stroke={C.series}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
              dot={{ r: 4, fill: C.series, stroke: C.surface, strokeWidth: 2 }}
              activeDot={{ r: 6, fill: C.series, stroke: C.surface, strokeWidth: 2 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-1 flex items-center justify-between text-xs text-muted">
        <span>
          {fmtMonth(spec.range.start ?? "")} – {fmtMonth(spec.range.end ?? "")} · {spec.review_count} reviews
        </span>
        <button onClick={() => setShowTable((v) => !v)} className="hover:text-ink">
          {showTable ? "Hide data" : "Show data"}
        </button>
      </div>
      {showTable && (
        <DataTable
          spec={{
            type: "table",
            columns: [
              { key: "month", label: "Month" },
              { key: "avg", label: "Avg rating", align: "right" },
              { key: "count", label: "Reviews", align: "right" },
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
    case "rating_trend":
      return <RatingTrend spec={spec} />;
    case "news_timeline":
      return <NewsTimeline spec={spec} />;
    case "table":
      return <DataTable spec={spec} />;
  }
}

/** The current version of a chart, as shown in the case file. */
export function ChartBlock({ chart, onTarget }: { chart: Chart; onTarget?: (t: FeedbackTarget) => void }) {
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
      <div className="mt-3">
        <Body spec={chart.spec} />
      </div>
    </section>
  );
}
