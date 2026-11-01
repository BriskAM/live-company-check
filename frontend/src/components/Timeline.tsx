"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useState } from "react";
import type { FeedbackTarget } from "@/lib/api";
import { CheckView, Feedback, Findings, Item, Message, Tool } from "@/lib/checkState";
import { ChartBlock } from "./ChartBlock";
import { QuestionCard } from "./QuestionCard";

const TOOL_LABELS: Record<string, string> = {
  search_companies: "Searching companies",
  get_company: "Reading company records",
  get_reviews: "Reading customer reviews",
  get_news: "Reading news",
  search_news: "Searching news",
};

type OnTarget = (t: FeedbackTarget) => void;

function TargetButton({ onClick, label = "Feedback" }: { onClick: () => void; label?: string }) {
  return (
    <button onClick={onClick} className="text-[11px] text-zinc-400 hover:text-blue-700">
      {label}
    </button>
  );
}

function Superseded({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-dashed border-zinc-200 px-4 py-2 text-xs text-zinc-500">
      <button onClick={() => setOpen((v) => !v)} className="hover:text-zinc-700">
        {label} {open ? "▾" : "▸"}
      </button>
      {open && <div className="mt-2 opacity-70">{children}</div>}
    </div>
  );
}

function ToolStep({ tool }: { tool: Tool }) {
  const running = tool.status === "started";
  const arg = tool.name === "search_news" ? ` “${tool.args?.text ?? ""}”` : "";
  return (
    <div className="flex items-center gap-2 text-sm text-zinc-500">
      <span className={`h-2 w-2 rounded-full ${running ? "animate-pulse bg-blue-500" : "bg-emerald-500"}`} />
      <span>
        {TOOL_LABELS[tool.name] ?? tool.name}
        {arg}
      </span>
      {tool.summary && <span className="text-zinc-400">· {tool.summary}</span>}
      <code className="ml-auto text-xs text-zinc-400">{tool.name}</code>
    </div>
  );
}

function Markdown({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <div className={`md text-sm leading-relaxed ${streaming ? "streaming" : ""}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

/** Report with the lines that are new since the previous version highlighted. */
function ReportDiff({ text, previous }: { text: string; previous: string }) {
  const before = new Set(previous.split("\n").map((l) => l.trim()));
  return (
    <div className="space-y-1">
      {text
        .split("\n")
        .filter((l) => l.trim())
        .map((line, i) => (
          <div
            key={i}
            className={before.has(line.trim()) ? "" : "-mx-2 rounded bg-emerald-50 px-2 ring-1 ring-emerald-100"}
          >
            <Markdown text={line} streaming={false} />
          </div>
        ))}
    </div>
  );
}

function ReportBlock({ message, onTarget }: { message: Message; onTarget?: OnTarget }) {
  const [diff, setDiff] = useState(false);
  const canDiff = message.done && !!message.previous;
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
          {message.previous ? "Report · revised" : "Report"}
        </h3>
        {canDiff && (
          <button onClick={() => setDiff((v) => !v)} className="text-[11px] text-zinc-500 hover:text-zinc-800">
            {diff ? "Hide changes" : "Highlight changes"}
          </button>
        )}
        {message.done && onTarget && (
          <span className="ml-auto">
            <TargetButton onClick={() => onTarget({ kind: "report" })} />
          </span>
        )}
      </div>
      {diff && message.previous ? (
        <ReportDiff text={message.text} previous={message.previous} />
      ) : (
        <Markdown text={message.text} streaming={!message.done} />
      )}
    </section>
  );
}

function MessageBlock({ message }: { message: Message }) {
  return (
    <div className="rounded-lg bg-white px-4 py-3 text-zinc-800 shadow-sm ring-1 ring-zinc-100">
      <Markdown text={message.text} streaming={!message.done} />
    </div>
  );
}

const SEVERITY: Record<string, string> = {
  high: "bg-red-50 text-red-700",
  medium: "bg-amber-50 text-amber-800",
  low: "bg-zinc-100 text-zinc-600",
};

function FindingsCard({ findings, onTarget }: { findings: Findings; onTarget?: OnTarget }) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-400">
        Concerns ({findings.concerns.length}){findings.changes ? " · updated" : ""}
      </h3>
      {findings.changes && findings.changes.length > 0 && (
        <ul className="mb-3 space-y-0.5 rounded-md bg-blue-50/60 px-3 py-2 text-xs text-blue-900">
          {findings.changes.map((c, i) => (
            <li key={i}>• {c}</li>
          ))}
        </ul>
      )}
      {findings.concerns.length === 0 && <p className="text-sm text-zinc-500">No concerns found.</p>}
      <ul className="space-y-2">
        {findings.concerns.map((c) => (
          <li key={c.id} className="text-sm">
            <div className="flex items-center gap-2">
              <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${SEVERITY[c.severity]}`}>
                {c.severity}
              </span>
              <span className="font-medium">{c.title}</span>
              <code className="text-[11px] text-zinc-400">{c.id}</code>
              {onTarget && (
                <span className="ml-auto">
                  <TargetButton onClick={() => onTarget({ kind: "concern", id: c.id })} />
                </span>
              )}
            </div>
            <p className="mt-0.5 text-zinc-600">{c.detail}</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {c.evidence_ids.map((id) => (
                <button
                  key={id}
                  title="Give feedback on this evidence"
                  onClick={() => onTarget?.({ kind: "evidence", id })}
                  className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[11px] text-zinc-600 hover:bg-blue-50 hover:text-blue-700"
                >
                  {id}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
      {findings.recommendation?.decision && (
        <p className="mt-3 border-t border-zinc-100 pt-2 text-sm">
          <span className="text-zinc-500">Recommendation: </span>
          <span className="font-medium">{findings.recommendation.decision.replaceAll("_", " ")}</span>
        </p>
      )}
    </section>
  );
}

function targetLabel(t: Feedback["target"]): string | null {
  if (!t) return null;
  return t.id ? `${t.kind} ${t.id}` : t.kind;
}

function FeedbackBubble({ feedback }: { feedback: Feedback }) {
  const r = feedback.result;
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-zinc-900 px-4 py-2 text-sm text-white">
        {targetLabel(feedback.target) && (
          <div className="mb-0.5 text-[11px] text-zinc-400">on {targetLabel(feedback.target)}</div>
        )}
        {feedback.text}
      </div>
      <div className="text-[11px] text-zinc-400">
        {r
          ? r.outcome === "duplicate"
            ? r.message
            : r.changes.length
              ? `Applied: ${r.changes.join("; ")}`
              : "Handled, nothing needed to change"
          : feedback.queued
            ? "Queued: handled when the agent is free"
            : "Working on it…"}
      </div>
    </div>
  );
}

function Marker({ text, tone }: { text: string; tone: "info" | "warn" | "error" }) {
  const color = { info: "text-zinc-400", warn: "text-amber-600", error: "text-red-600" }[tone];
  return (
    <div className={`flex items-center gap-3 text-xs ${color}`}>
      <div className="h-px flex-1 bg-zinc-200" />
      {text}
      <div className="h-px flex-1 bg-zinc-200" />
    </div>
  );
}

function itemKey(item: Item): string {
  return "id" in item ? `${item.kind}:${item.id}` : `${item.kind}:${item.seq}`;
}

export function Timeline({
  view,
  checkId,
  onTarget,
}: {
  view: CheckView;
  checkId: string;
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
            if (m.kind !== "report") return <MessageBlock key={key} message={m} />;
            if (view.latestReport !== m.id)
              return (
                <Superseded key={key} label="Report — revised below">
                  <Markdown text={m.text} streaming={false} />
                </Superseded>
              );
            return <ReportBlock key={key} message={m} onTarget={onTarget} />;
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
          case "findings":
            return view.latestFindings === item.seq ? (
              <FindingsCard key={key} findings={item.findings} onTarget={onTarget} />
            ) : (
              <Superseded key={key} label={`Concerns (${item.findings.concerns.length}) — updated below`}>
                <FindingsCard findings={item.findings} />
              </Superseded>
            );
          case "feedback":
            return <FeedbackBubble key={key} feedback={view.feedback[item.id]} />;
          case "excluded":
            return (
              <div key={key} className="rounded-lg bg-zinc-100 px-3 py-2 text-xs text-zinc-600">
                Left out {item.ids.join(", ")} — {item.reason}
              </div>
            );
          case "chart":
            return (
              <ChartBlock
                key={key}
                chart={item.chart}
                superseded={view.latestChart[item.chart.chart_id] > item.chart.version}
                onTarget={onTarget}
              />
            );
          case "company":
            return (
              <div key={key} className="text-sm text-zinc-600">
                Checking <span className="font-medium text-zinc-900">{item.name}</span>
              </div>
            );
          case "marker":
            return <Marker key={key} text={item.text} tone={item.tone} />;
        }
      })}
    </div>
  );
}
