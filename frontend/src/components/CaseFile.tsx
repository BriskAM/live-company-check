"use client";

import { useState } from "react";
import { CheckView, Company, Findings, Message } from "@/lib/checkState";
import { ChartBlock } from "./ChartBlock";
import { Markdown } from "./Markdown";
import type { OnTarget } from "./Timeline";

/**
 * The current state of the check: company, findings, charts and report, always at their
 * latest version. The activity stream is the history; this is the answer.
 */

function Section({
  id,
  label,
  action,
  children,
}: {
  id: string;
  label: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-4 rounded-md border border-line bg-card p-4">
      <div className="mb-3 flex items-center gap-3">
        <h2 className="label">{label}</h2>
        {action && <div className="ml-auto flex items-center gap-3">{action}</div>}
      </div>
      {children}
    </section>
  );
}

function TargetButton({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} className="text-[11px] text-muted hover:text-accent">
      Feedback
    </button>
  );
}

function CompanyCard({ company }: { company: Company }) {
  const active = company.status === "active";
  const rows: [string, string | undefined][] = [
    ["Founded", company.founded],
    ["Closed", company.closed_on],
    ["Address", company.address],
    ["Previously", company.previous_names?.join(", ")],
  ];
  return (
    <Section id="cf-company" label="Company">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-lg font-bold tracking-tight text-ink">{company.name}</h3>
        {company.status && (
          <span
            className={`rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold uppercase ${
              active ? "bg-ok/15 text-ok" : "bg-bad/15 text-bad"
            }`}
          >
            {company.status}
          </span>
        )}
        <code className="ml-auto font-mono text-[11px] text-muted">{company.company_id}</code>
      </div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
        {rows
          .filter(([, v]) => v)
          .map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{k}</dt>
              <dd className="text-ink">{v}</dd>
            </div>
          ))}
      </dl>
    </Section>
  );
}

const SEVERITY: Record<string, string> = {
  high: "bg-bad/15 text-bad",
  medium: "bg-warn/15 text-warn",
  low: "bg-chip text-muted",
};

const DECISION: Record<string, { label: string; cls: string }> = {
  proceed: { label: "Proceed", cls: "border-ok/40 bg-ok/10 text-ok" },
  proceed_with_caution: { label: "Proceed with caution", cls: "border-warn/40 bg-warn/10 text-warn" },
  do_not_proceed: { label: "Do not proceed", cls: "border-bad/40 bg-bad/10 text-bad" },
  needs_more_info: { label: "Needs more information", cls: "border-info/40 bg-info/10 text-info" },
};

function FindingsSection({ findings, onTarget }: { findings: Findings; onTarget?: OnTarget }) {
  const decision = DECISION[findings.recommendation?.decision ?? ""];
  return (
    <Section id="cf-findings" label={`Findings · ${findings.concerns.length} concern${findings.concerns.length === 1 ? "" : "s"}`}>
      {decision && (
        <div className={`mb-4 rounded-md border px-3 py-2 ${decision.cls}`}>
          <div className="font-mono text-sm font-bold">{decision.label}</div>
          {findings.recommendation.reason && (
            <p className="mt-0.5 text-sm text-ink">{findings.recommendation.reason}</p>
          )}
        </div>
      )}
      {findings.concerns.length === 0 && <p className="text-sm text-muted">No concerns found.</p>}
      <ul className="space-y-3">
        {findings.concerns.map((c) => (
          <li key={c.id} className="text-sm">
            <div className="flex items-start gap-2">
              <span className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase ${SEVERITY[c.severity]}`}>
                {c.severity}
              </span>
              <span className="font-medium text-ink">{c.title}</span>
              <span className="ml-auto mt-0.5 flex shrink-0 items-center gap-3">
                <code className="font-mono text-[11px] text-muted">{c.id}</code>
                {onTarget && <TargetButton onClick={() => onTarget({ kind: "concern", id: c.id })} />}
              </span>
            </div>
            <p className="mt-1 text-muted">{c.detail}</p>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {c.evidence_ids.map((id) => (
                <button
                  key={id}
                  title="Give feedback on this evidence"
                  onClick={() => onTarget?.({ kind: "evidence", id })}
                  className="rounded bg-chip px-1.5 py-0.5 font-mono text-[11px] text-muted hover:bg-accent/15 hover:text-accent"
                >
                  {id}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
      {findings.open_questions.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <h3 className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted">Open questions</h3>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-ink marker:text-muted">
            {findings.open_questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      )}
    </Section>
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
          <div key={i} className={before.has(line.trim()) ? "" : "-mx-2 rounded bg-ok/10 px-2 ring-1 ring-ok/30"}>
            <Markdown text={line} streaming={false} />
          </div>
        ))}
    </div>
  );
}

function ReportSection({ message, onTarget }: { message: Message; onTarget?: OnTarget }) {
  const [diff, setDiff] = useState(false);
  const canDiff = message.done && !!message.previous;
  return (
    <Section
      id="cf-report"
      label={message.previous ? "Report · revised" : "Report"}
      action={
        <>
          {canDiff && (
            <button onClick={() => setDiff((v) => !v)} className="text-[11px] text-muted hover:text-ink">
              {diff ? "Hide changes" : "Highlight changes"}
            </button>
          )}
          {message.done && onTarget && <TargetButton onClick={() => onTarget({ kind: "report" })} />}
        </>
      }
    >
      {diff && message.previous ? (
        <ReportDiff text={message.text} previous={message.previous} />
      ) : (
        <div className="text-ink">
          <Markdown text={message.text} streaming={!message.done} />
        </div>
      )}
    </Section>
  );
}

export function CaseFile({ view, onTarget }: { view: CheckView; onTarget?: OnTarget }) {
  const report = view.latestReport ? view.messages[view.latestReport] : undefined;
  const charts = Object.values(view.charts);
  const empty = !view.company && !view.findings && charts.length === 0 && !report;
  return (
    <div className="space-y-4">
      {empty && (
        <div className="rounded-md border border-dashed border-line p-6 text-center text-sm text-muted">
          The case file fills in as the agent works: company, findings, charts and the report.
        </div>
      )}
      {view.company && <CompanyCard company={view.company} />}
      {view.findings && <FindingsSection findings={view.findings} onTarget={onTarget} />}
      {charts.map((c) => (
        <div key={c.chart_id} id={`cf-${c.chart_id}`} className="scroll-mt-4 rounded-md">
          <ChartBlock chart={c} onTarget={onTarget} />
        </div>
      ))}
      {report && (report.text || !report.done) && <ReportSection message={report} onTarget={onTarget} />}
    </div>
  );
}
