"use client";

import Link from "next/link";
import { use, useEffect, useRef, useState } from "react";
import { CaseFile } from "@/components/CaseFile";
import { FeedbackBox } from "@/components/FeedbackBox";
import { StatusBadge } from "@/components/StatusBadge";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Timeline } from "@/components/Timeline";
import { api, Check, FeedbackTarget, newCommandId } from "@/lib/api";
import { CheckView, progress, Step } from "@/lib/checkState";
import { useCheckEvents } from "@/lib/useCheckEvents";

type Control = "pause" | "resume" | "retry";
type Tab = "activity" | "case";

const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

function Usage({ usage }: { usage: CheckView["usage"] }) {
  if (!usage.calls) return null;
  return (
    <span
      className="hidden font-mono text-[11px] text-muted md:inline"
      title={`${usage.calls} LLM calls · ${usage.input.toLocaleString()} tokens in, ${usage.output.toLocaleString()} out · ${usage.models.join(", ")}`}
    >
      {fmt(usage.input + usage.output)} tokens · {usage.calls} calls
    </span>
  );
}

function Progress({ steps, working }: { steps: Step[]; working: boolean }) {
  return (
    <ol className="flex items-center gap-1.5 font-mono text-[11px]">
      {steps.map((s, i) => (
        <li key={s.label} className="flex items-center gap-1.5">
          {i > 0 && <span className={`h-px w-4 ${s.state === "todo" ? "bg-line" : "bg-accent"}`} />}
          <span
            className={`flex items-center gap-1 ${
              s.state === "done" ? "text-ink" : s.state === "active" ? "text-accent" : "text-muted opacity-60"
            }`}
          >
            <span
              className={`h-2 w-2 rounded-sm ${
                s.state === "done"
                  ? "bg-accent"
                  : s.state === "active"
                    ? `border border-accent ${working ? "animate-pulse bg-accent/40" : ""}`
                    : "border border-line"
              }`}
            />
            {s.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

export default function CheckPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { view, connected } = useCheckEvents(id);
  const [check, setCheck] = useState<Check | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState<Control | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const [target, setTarget] = useState<FeedbackTarget | null>(null);
  const [tab, setTab] = useState<Tab>("activity");
  const bottom = useRef<HTMLDivElement>(null);
  const caseFile = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  useEffect(() => {
    api.getCheck(id).then(setCheck).catch((e: Error) => setLoadError(e.message));
  }, [id]);

  // Follow new activity unless the reviewer has scrolled up to read.
  useEffect(() => {
    const onScroll = () => {
      stickToBottom.current = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
    };
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (stickToBottom.current && tab === "activity") bottom.current?.scrollIntoView({ block: "end" });
  }, [view, tab]);

  // Clear the "pausing…" state once the server confirms the new status.
  useEffect(() => setPending(null), [view.status]);

  async function control(action: Control) {
    setPending(action);
    setControlError(null);
    try {
      await api.control(id, action, newCommandId());
    } catch (e) {
      setControlError((e as Error).message);
      setPending(null);
    }
  }

  /** Jump from a row in the activity stream to that section of the case file. */
  function show(sectionId: string) {
    setTab("case");
    requestAnimationFrame(() => {
      const el = document.getElementById(sectionId);
      const panel = caseFile.current;
      if (!el || !panel) return;
      if (panel.scrollHeight > panel.clientHeight) {
        // Wide screens: scroll only the case file, leaving the activity column where it is.
        const top = el.getBoundingClientRect().top - panel.getBoundingClientRect().top + panel.scrollTop;
        panel.scrollTo({ top: top - 8, behavior: "smooth" });
      } else {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      el.classList.remove("flash");
      void el.offsetWidth; // restart the animation
      el.classList.add("flash");
    });
  }

  function giveFeedback(t: FeedbackTarget) {
    setTarget(t);
    setTab("activity");
    requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>("#feedback-input")?.focus());
  }

  if (loadError) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-12">
        <p className="text-sm text-bad">Couldn’t load this check: {loadError}</p>
        <Link href="/" className="mt-4 inline-block text-sm text-muted underline">
          Back
        </Link>
      </main>
    );
  }

  const status = view.status;
  const canPause = status === "running" || status === "waiting_for_input";
  const button = "rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50";

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <Link
            href="/"
            className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-accent font-mono text-xs font-bold text-on-accent"
            title="All checks"
          >
            CC
          </Link>
          <h1 className="min-w-0 truncate font-mono text-base font-bold tracking-tight">{check?.input ?? "…"}</h1>
          <StatusBadge status={status} />
          {!connected && <span className="font-mono text-[11px] text-muted">reconnecting…</span>}
          <div className="hidden lg:block">
            <Progress steps={progress(view)} working={status === "running"} />
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Usage usage={view.usage} />
            {canPause && (
              <button
                onClick={() => control("pause")}
                disabled={pending !== null}
                className={`${button} border border-line bg-card text-ink hover:border-accent`}
              >
                {pending === "pause" ? "Pausing…" : "Pause"}
              </button>
            )}
            {status === "paused" && (
              <button onClick={() => control("resume")} disabled={pending !== null} className={`${button} bg-accent text-on-accent`}>
                {pending === "resume" ? "Resuming…" : "Resume"}
              </button>
            )}
            {status === "error" && (
              <button onClick={() => control("retry")} disabled={pending !== null} className={`${button} bg-accent text-on-accent`}>
                {pending === "retry" ? "Retrying…" : "Retry"}
              </button>
            )}
            <ThemeToggle />
          </div>
        </div>
        {(controlError || view.error) && (
          <div className="mx-auto max-w-7xl px-4 pb-3 text-xs text-bad">
            {controlError && <p>{controlError}</p>}
            {view.error && (
              <div>
                {view.error}
                {view.errorDetail && (
                  <details className="mt-1 text-muted">
                    <summary className="cursor-pointer">Details</summary>
                    <code className="break-all font-mono">{view.errorDetail}</code>
                  </details>
                )}
              </div>
            )}
          </div>
        )}
        {/* Narrow screens: one column at a time. */}
        <div className="mx-auto flex max-w-7xl gap-1 px-4 pb-2 lg:hidden">
          {(["activity", "case"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => {
                setTab(t);
                window.scrollTo({ top: t === "case" ? 0 : document.body.scrollHeight });
              }}
              className={`rounded px-3 py-1 font-mono text-[11px] font-semibold uppercase tracking-wider ${
                tab === t ? "bg-accent/15 text-accent" : "text-muted hover:text-ink"
              }`}
            >
              {t === "activity" ? "Activity" : "Case file"}
            </button>
          ))}
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl grid-cols-[minmax(0,1fr)] gap-6 px-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <main className={`flex min-h-[calc(100vh-4rem)] flex-col ${tab === "activity" ? "" : "hidden lg:flex"}`}>
          <div className="flex-1 pb-6 pt-6">
            <h2 className="label mb-3 hidden lg:block">Activity</h2>
            {view.items.length === 0 ? (
              <p className="text-sm text-muted">Starting the agent…</p>
            ) : (
              <Timeline view={view} checkId={id} onShow={show} />
            )}
            <div ref={bottom} />
          </div>
          {view.items.length > 0 && (
            <FeedbackBox checkId={id} status={status} target={target} onClearTarget={() => setTarget(null)} />
          )}
        </main>

        <aside className={`${tab === "case" ? "" : "hidden"} pb-10 pt-6 lg:block`}>
          <div
            ref={caseFile}
            className="lg:sticky lg:top-[4.5rem] lg:max-h-[calc(100vh-5.5rem)] lg:overflow-y-auto lg:pr-1"
          >
            <h2 className="label mb-3 hidden lg:block">Case file</h2>
            <CaseFile view={view} onTarget={giveFeedback} />
          </div>
        </aside>
      </div>
    </div>
  );
}
