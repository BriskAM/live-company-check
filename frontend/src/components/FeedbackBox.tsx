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
    <div className="fixed inset-x-0 bottom-0 z-10 border-t border-zinc-200 bg-[#f7f7f8]/95 backdrop-blur">
      <form
        className="mx-auto max-w-3xl px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        {target && (
          <div className="mb-2 flex items-center gap-2 text-xs">
            <span className="rounded bg-blue-50 px-2 py-0.5 text-blue-800">
              About {target.kind}
              {target.id ? ` ${target.id}` : ""}
            </span>
            <button type="button" onClick={onClearTarget} className="text-zinc-400 hover:text-zinc-700">
              clear
            </button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <textarea
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
            className="max-h-40 min-h-[40px] flex-1 resize-y rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={sending || !text.trim()}
            className="rounded-lg bg-zinc-900 px-4 py-2 text-sm text-white disabled:opacity-40"
          >
            {sending ? "Sending…" : "Send"}
          </button>
        </div>
        {error && <p className="mt-1 text-xs text-red-600">Couldn’t send: {error}. Try again.</p>}
      </form>
    </div>
  );
}
