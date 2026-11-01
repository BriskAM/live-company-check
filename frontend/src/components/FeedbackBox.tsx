"use client";

import { useRef, useState } from "react";
import { api, FeedbackTarget, newCommandId } from "@/lib/api";

/**
 * Reviewer feedback, pinned to the bottom of the check. It can be sent at any time: while
 * the agent is busy, the workflow queues it. A target (chart, concern, evidence, report) is
 * set by the "Feedback" buttons in the timeline.
 */
export function FeedbackBox({
  checkId,
  status,
  target,
  onClearTarget,
}: {
  checkId: string;
  status: string;
  target: FeedbackTarget | null;
  onClearTarget: () => void;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One id per message; kept if sending fails so a retry is recognised as the same command.
  const commandId = useRef<string | null>(null);

  async function send() {
    const body = text.trim();
    if (!body || sending) return;
    commandId.current ??= newCommandId();
    setSending(true);
    setError(null);
    try {
      await api.feedback(checkId, commandId.current, body, target);
      commandId.current = null;
      setText("");
      onClearTarget();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  const busy = status !== "completed";
  return (
    // Sticks to the bottom of the activity column (not the whole window, which also holds the
    // case file on wide screens).
    <div className="sticky bottom-0 z-10 -mx-4 border-t border-line bg-bg/95 backdrop-blur">
      <form
        className="px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        {target && (
          <div className="mb-2 flex items-center gap-2 text-xs">
            <span className="rounded bg-accent/15 px-2 py-0.5 font-mono text-accent">
              About {target.kind}
              {target.id ? ` ${target.id}` : ""}
            </span>
            <button type="button" onClick={onClearTarget} className="text-muted hover:text-ink">
              clear
            </button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <textarea
            id="feedback-input"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              commandId.current = null; // edited text is a new message
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={1}
            placeholder={
              busy ? "Send feedback (handled when the agent is free)…" : "Give feedback or ask for a change…"
            }
            className="max-h-40 min-h-[40px] flex-1 resize-y rounded-md border border-line bg-card text-ink placeholder:text-muted outline-none focus:border-accent px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={sending || !text.trim()}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-on-accent disabled:opacity-40"
          >
            {sending ? "Sending…" : "Send"}
          </button>
        </div>
        {error && <p className="mt-1 text-xs text-bad">Couldn’t send: {error}. Try again.</p>}
      </form>
    </div>
  );
}
