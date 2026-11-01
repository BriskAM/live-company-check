"use client";

import type { FeedbackTarget } from "@/lib/api";
import { CheckView, Feedback, Item, Tool } from "@/lib/checkState";
import { ChartBlock } from "./ChartBlock";
import { Markdown } from "./Markdown";
import { QuestionCard } from "./QuestionCard";

/**
 * The activity stream: what the agent did, in order. Questions, agent notes, reviewer feedback
 * and charts appear in full, each chart version where the agent made it; findings and the
 * report appear as compact rows that jump to their current version in the case file.
 */

const TOOL_LABELS: Record<string, string> = {
  search_companies: "Searching companies",
  get_company: "Reading company records",
  get_reviews: "Reading customer reviews",
  get_news: "Reading news",
  search_news: "Searching news",
};

export type OnTarget = (t: FeedbackTarget) => void;
export type OnShow = (sectionId: string) => void;

function ToolStep({ tool }: { tool: Tool }) {
  const running = tool.status === "started";
  const arg = tool.name === "search_news" ? ` “${tool.args?.text ?? ""}”` : "";
  return (
    <div className="flex items-center gap-2 pl-1 text-[13px] text-muted">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${running ? "animate-pulse bg-accent" : "bg-ok"}`} />
      <span className="truncate">
        {TOOL_LABELS[tool.name] ?? tool.name}
        {arg}
        {tool.summary && <span className="opacity-70"> · {tool.summary}</span>}
      </span>
      <code className="ml-auto shrink-0 font-mono text-[11px] opacity-60">{tool.name}</code>
    </div>
  );
}

/** A compact row pointing at something that lives in the case file. */
function Pointer({
  label,
  title,
  meta,
  onView,
  children,
}: {
  label: string;
  title: React.ReactNode;
  meta?: React.ReactNode;
  onView: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-line bg-card px-3 py-2">
      <div className="flex items-center gap-3">
        <span className="label w-16 shrink-0">{label}</span>
        <span className="min-w-0 flex-1 truncate text-sm text-ink">{title}</span>
        {meta && <span className="shrink-0 font-mono text-[11px] text-muted">{meta}</span>}
        <button onClick={onView} className="shrink-0 text-xs font-medium text-accent hover:underline">
          View
        </button>
      </div>
      {children}
    </div>
  );
}

function targetLabel(t: Feedback["target"]): string | null {
  if (!t) return null;
  return t.id ? `${t.kind} ${t.id}` : t.kind;
}

function FeedbackBubble({ feedback }: { feedback: Feedback }) {
  const r = feedback.result;
  const status = r
    ? r.outcome === "duplicate"
      ? r.message
      : r.changes.length
        ? `Applied: ${r.changes.join("; ")}`
        : "Handled, nothing needed to change"
    : feedback.queued
      ? "Queued: handled when the agent is free"
      : "Working on it…";
  return (
    <div className="flex flex-col items-end gap-1 pl-10">
      <div className="rounded-lg rounded-br-sm bg-accent px-3.5 py-2 text-sm text-on-accent">
        {targetLabel(feedback.target) && (
          <div className="mb-0.5 font-mono text-[11px] opacity-75">on {targetLabel(feedback.target)}</div>
        )}
        {feedback.text}
      </div>
      <div className={`text-right text-[11px] ${r?.outcome === "applied" ? "text-ok" : "text-muted"}`}>{status}</div>
    </div>
  );
}

function Marker({ text, tone }: { text: string; tone: "info" | "warn" | "error" }) {
  const color = { info: "text-muted", warn: "text-warn", error: "text-bad" }[tone];
  return (
    <div className={`flex items-center gap-3 font-mono text-[11px] uppercase tracking-wider ${color}`}>
      <div className="h-px flex-1 bg-line" />
      {text}
      <div className="h-px flex-1 bg-line" />
    </div>
  );
}

function itemKey(item: Item): string {
  return "id" in item ? `${item.kind}:${item.id}` : `${item.kind}:${item.seq}`;
}

export function Timeline({
  view,
  checkId,
  onShow,
  onTarget,
}: {
  view: CheckView;
  checkId: string;
  onShow: OnShow;
  onTarget?: OnTarget;
}) {
  return (
    <div className="space-y-3">
      {view.items.map((item) => {
        const key = itemKey(item);
        switch (item.kind) {
          case "tool":
            return <ToolStep key={key} tool={view.tools[item.id]} />;
          case "message": {
            const m = view.messages[item.id];
            if (!m || (!m.text && m.done)) return null;
            if (m.kind === "report")
              return (
                <Pointer
                  key={key}
                  label="Report"
                  title={!m.done ? "Writing the report…" : m.previous ? "Report revised" : "Report written"}
                  meta={m.done ? `${m.text.split(/\s+/).length} words` : undefined}
                  onView={() => onShow("cf-report")}
                >
                  {!m.done && m.text && (
                    // Live tail of the text being written, so the stream still feels live.
                    <p className="streaming mt-1.5 line-clamp-2 pl-[76px] text-xs text-muted">
                      {m.text.slice(-220).replace(/[#*_`]/g, "")}
                    </p>
                  )}
                </Pointer>
              );
            return (
              <div key={key} className="rounded-md border border-line bg-card px-4 py-3 text-ink">
                <Markdown text={m.text} streaming={!m.done} />
              </div>
            );
          }
          case "question":
            return (
              <QuestionCard
                key={key}
                checkId={checkId}
                question={view.questions[item.id]}
                paused={view.status === "paused"}
              />
            );
          case "findings": {
            const f = item.findings;
            return (
              <Pointer
                key={key}
                label="Findings"
                title={`${f.concerns.length} concern${f.concerns.length === 1 ? "" : "s"}${
                  f.recommendation?.decision ? ` · ${f.recommendation.decision.replaceAll("_", " ")}` : ""
                }`}
                meta={f.changes ? "updated" : undefined}
                onView={() => onShow("cf-findings")}
              >
                {f.changes && f.changes.length > 0 && (
                  <ul className="mt-1.5 space-y-0.5 pl-[76px] text-xs text-muted">
                    {f.changes.map((c, i) => (
                      <li key={i}>· {c}</li>
                    ))}
                  </ul>
                )}
              </Pointer>
            );
          }
          case "chart": {
            const latest = view.charts[item.chart.chart_id]?.version ?? item.chart.version;
            const old = latest > item.chart.version;
            return (
              <div key={key} className={old ? "opacity-60" : ""}>
                <ChartBlock
                  chart={item.chart}
                  onTarget={old ? undefined : onTarget}
                  note={
                    old && (
                      <>
                        Updated since: v{latest} ·{" "}
                        <button onClick={() => onShow(`cf-${item.chart.chart_id}`)} className="text-accent hover:underline">
                          View latest
                        </button>
                      </>
                    )
                  }
                />
              </div>
            );
          }
          case "company":
            return (
              <div key={key} className="pl-1 text-sm text-muted">
                Checking <span className="font-medium text-ink">{item.name}</span>
              </div>
            );
          case "feedback":
            return <FeedbackBubble key={key} feedback={view.feedback[item.id]} />;
          case "excluded":
            return (
              <div key={key} className="rounded-md bg-chip px-3 py-2 text-xs text-muted">
                Left out <span className="font-mono text-ink">{item.ids.join(", ")}</span> — {item.reason}
              </div>
            );
          case "marker":
            return <Marker key={key} text={item.text} tone={item.tone} />;
        }
      })}
    </div>
  );
}
