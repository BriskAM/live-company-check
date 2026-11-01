"use client";

import Link from "next/link";
import { use, useEffect, useRef, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { Timeline } from "@/components/Timeline";
import { api, Check, newCommandId } from "@/lib/api";
import { useCheckEvents } from "@/lib/useCheckEvents";

type Control = "pause" | "resume" | "retry";

export default function CheckPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { view, connected } = useCheckEvents(id);
  const [check, setCheck] = useState<Check | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState<Control | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  useEffect(() => {
    api.getCheck(id).then(setCheck).catch((e: Error) => setLoadError(e.message));
  }, [id]);

  // Follow new output unless the reviewer has scrolled up to read.
  useEffect(() => {
    const onScroll = () => {
      stickToBottom.current = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
    };
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (stickToBottom.current) bottom.current?.scrollIntoView({ block: "end" });
  }, [view]);

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

  if (loadError) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-12">
        <p className="text-sm text-red-600">Couldn’t load this check: {loadError}</p>
        <Link href="/" className="mt-4 inline-block text-sm text-zinc-500 underline">
          Back
        </Link>
      </main>
    );
  }

  const status = view.status;
  const canPause = status === "running" || status === "waiting_for_input";

  return (
    <main className="mx-auto max-w-3xl px-4 pb-24">
      <header className="sticky top-0 z-10 -mx-4 mb-6 border-b border-zinc-200 bg-[#f7f7f8]/95 px-4 py-4 backdrop-blur">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-sm text-zinc-400 hover:text-zinc-700">
            ←
          </Link>
          <h1 className="truncate text-lg font-semibold">{check?.input ?? "…"}</h1>
          <StatusBadge status={status} />
          {!connected && <span className="text-xs text-zinc-400">reconnecting…</span>}
          <div className="ml-auto flex gap-2">
            {canPause && (
              <button
                onClick={() => control("pause")}
                disabled={pending !== null}
                className="rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-sm disabled:opacity-50"
              >
                {pending === "pause" ? "Pausing…" : "Pause"}
              </button>
            )}
            {status === "paused" && (
              <button
                onClick={() => control("resume")}
                disabled={pending !== null}
                className="rounded-lg bg-zinc-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                {pending === "resume" ? "Resuming…" : "Resume"}
              </button>
            )}
            {status === "error" && (
              <button
                onClick={() => control("retry")}
                disabled={pending !== null}
                className="rounded-lg bg-zinc-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                {pending === "retry" ? "Retrying…" : "Retry"}
              </button>
            )}
          </div>
        </div>
        {controlError && <p className="mt-2 text-xs text-red-600">{controlError}</p>}
        {view.error && <p className="mt-2 text-xs text-red-600">{view.error}</p>}
      </header>

      {view.items.length === 0 ? (
        <p className="text-sm text-zinc-400">Starting the agent…</p>
      ) : (
        <Timeline view={view} checkId={id} />
      )}
      <div ref={bottom} />
    </main>
  );
}
