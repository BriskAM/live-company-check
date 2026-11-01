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
import type { Chart, ChartSpec, RatingTrendSpec, NewsTimelineSpec, TableSpec } from "@/lib/checkState";

// Chart tokens (light surface). One series → no legend; the title names it.
const C = {
  surface: "#ffffff",
  series: "#2a78d6",
  grid: "#ececea",
  axis: "#8a8984",
  textPrimary: "#0b0b0b",
  textSecondary: "#52514e",
  annotation: "#52514e",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtMonth = (m: string) => `${MONTHS[Number(m.slice(5, 7)) - 1]} ’${m.slice(2, 4)}`;

function RatingTooltip({ active, payload }: { active?: boolean; payload?: { payload: { month: string; avg: number; count: number } }[] }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-md border border-zinc-200 bg-white px-2.5 py-1.5 text-xs shadow-sm">
      <div className="font-medium" style={{ color: C.textPrimary }}>{fmtMonth(d.month)}</div>
      <div style={{ color: C.textSecondary }}>
        Avg rating <span className="font-medium tabular-nums" style={{ color: C.textPrimary }}>{d.avg.toFixed(2)}</span>
        {" · "}
        {d.count} review{d.count === 1 ? "" : "s"}
      </div>
    </div>
  );
}

function RatingTrend({ spec }: { spec: RatingTrendSpec }) {
  const [showTable, setShowTable] = useState(false);
  if (spec.data.length === 0) return <p className="text-sm text-zinc-500">No reviews in this range.</p>;
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
                stroke={C.annotation}
                strokeWidth={1}
                label={{ value: m.label, position: "top", fontSize: 11, fill: C.textSecondary }}
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
      <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
        <span>
          {fmtMonth(spec.range.start ?? "")} – {fmtMonth(spec.range.end ?? "")} · {spec.review_count} reviews
        </span>
        <button onClick={() => setShowTable((v) => !v)} className="underline-offset-2 hover:underline">
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
  if (spec.events.length === 0) return <p className="text-sm text-zinc-500">No news items.</p>;
  return (
    <div>
      <ol className="relative ml-1.5 border-l border-zinc-200">
        {spec.events.map((e) => (
          <li key={e.id} className="relative pb-3 pl-4 last:pb-0">
            <span
              className="absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-white"
              style={{ background: C.series }}
            />
            <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-zinc-500">
              <time className="tabular-nums">{e.date}</time>
              <code className="text-[11px] text-zinc-400">{e.id}</code>
            </div>
            <div className="text-sm font-medium text-zinc-900">{e.headline}</div>
            <div className="text-sm text-zinc-600">{e.text}</div>
          </li>
        ))}
      </ol>
      {spec.excluded.length > 0 && (
        <p className="mt-2 text-xs text-zinc-500">Left out (excluded evidence): {spec.excluded.join(", ")}</p>
      )}
    </div>
  );
}

function DataTable({ spec }: { spec: TableSpec }) {
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-200 text-left text-xs text-zinc-500">
            {spec.columns.map((c) => (
              <th key={c.key} className={`px-2 py-1.5 font-medium ${c.align === "right" ? "text-right" : ""}`}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {spec.rows.map((row, i) => (
            <tr key={i} className="border-b border-zinc-100 last:border-0">
              {spec.columns.map((c) => (
                <td
                  key={c.key}
                  className={`px-2 py-1.5 text-zinc-800 ${c.align === "right" ? "text-right tabular-nums" : ""}`}
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

/** One version of a chart. Older versions collapse to a one-line note pointing to the newer one. */
export function ChartBlock({ chart, superseded }: { chart: Chart; superseded: boolean }) {
  const [open, setOpen] = useState(false);
  const header = (
    <div className="flex items-center gap-2">
      <h3 className="text-sm font-medium text-zinc-900">{chart.title}</h3>
      {chart.version > 1 && (
        <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-700">
          updated · v{chart.version}
        </span>
      )}
      <code className="ml-auto text-[11px] text-zinc-400">{chart.chart_id}</code>
    </div>
  );

  if (superseded) {
    return (
      <div className="rounded-lg border border-dashed border-zinc-200 px-4 py-2 text-xs text-zinc-500">
        <button onClick={() => setOpen((v) => !v)} className="hover:text-zinc-700">
          {chart.title} (v{chart.version}) — updated below {open ? "▾" : "▸"}
        </button>
        {open && (
          <div className="mt-2 opacity-70">
            <Body spec={chart.spec} />
          </div>
        )}
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      {header}
      <div className="mt-3">
        <Body spec={chart.spec} />
      </div>
    </section>
  );
}
