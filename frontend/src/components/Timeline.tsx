"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CheckView, Findings, Item, Message, Tool } from "@/lib/checkState";
import { ChartBlock } from "./ChartBlock";
import { QuestionCard } from "./QuestionCard";

const TOOL_LABELS: Record<string, string> = {
  search_companies: "Searching companies",
  get_company: "Reading company records",
  get_reviews: "Reading customer reviews",
  get_news: "Reading news",
  search_news: "Searching news",
};

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

function MessageBlock({ message }: { message: Message }) {
  if (message.kind === "report") {
    return (
      <section className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-400">Report</h3>
        <Markdown text={message.text} streaming={!message.done} />
      </section>
    );
  }
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

function FindingsCard({ findings }: { findings: Findings }) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-400">
        Concerns ({findings.concerns.length})
      </h3>
      {findings.concerns.length === 0 && <p className="text-sm text-zinc-500">No concerns found.</p>}
      <ul className="space-y-2">
        {findings.concerns.map((c) => (
          <li key={c.id} className="text-sm">
            <div className="flex items-center gap-2">
              <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${SEVERITY[c.severity]}`}>
                {c.severity}
              </span>
              <span className="font-medium">{c.title}</span>
            </div>
            <p className="mt-0.5 text-zinc-600">{c.detail}</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {c.evidence_ids.map((id) => (
                <code key={id} className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-600">
                  {id}
                </code>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
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
}: {
  view: CheckView;
  checkId: string;
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
            return m && (m.text || !m.done) ? <MessageBlock key={key} message={m} /> : null;
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
            return <FindingsCard key={key} findings={item.findings} />;
          case "chart":
            return (
              <ChartBlock
                key={key}
                chart={item.chart}
                superseded={view.latestChart[item.chart.chart_id] > item.chart.version}
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
