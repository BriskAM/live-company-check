"use client";

import { useRef, useState } from "react";
import { api, newCommandId } from "@/lib/api";
import { Question } from "@/lib/checkState";

/**
 * Interactive question from the agent. Once answered (an `answer` event arrived) it shows
 * the answer read-only. While a submit is in flight the controls are locked, and the same
 * command id is reused if the reviewer retries after an error.
 */
export function QuestionCard({
  checkId,
  question,
  paused,
}: {
  checkId: string;
  question: Question;
  paused: boolean;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const commandId = useRef<string | null>(null);

  const answered = question.answer !== undefined;

  async function submit(value: unknown) {
    if (sending || answered) return;
    commandId.current ??= newCommandId();
    setSending(true);
    setError(null);
    try {
      await api.answer(checkId, commandId.current, question.question_id, value);
    } catch (e) {
      setError((e as Error).message);
      setSending(false); // allow a retry with the same command id
    }
  }

  const label = (id: string) => question.options.find((o) => o.id === id)?.label ?? id;
  const locked = answered || sending;

  return (
    <section
      className={`rounded-md border p-4 ${answered ? "border-line bg-card" : "border-accent/70 bg-accent/10 shadow-sm"}`}
    >
      <div className="label mb-1.5">{answered ? "Question" : "Question for you"}</div>
      <p className="text-sm font-medium text-ink">{question.question}</p>

      {answered ? (
        <p className="mt-2 text-sm text-ink">
          <span className="text-muted">You answered: </span>
          {Array.isArray(question.answer)
            ? (question.answer as string[]).map(label).join(", ")
            : question.kind === "single"
              ? label(String(question.answer))
              : String(question.answer)}
        </p>
      ) : (
        <div className="mt-3">
          {question.kind === "single" && (
            <div className="flex flex-col gap-2">
              {question.options.map((o) => (
                <button
                  key={o.id}
                  disabled={locked}
                  onClick={() => submit(o.id)}
                  className="rounded-md border border-line bg-card px-3 py-2 text-left text-sm text-ink hover:border-accent disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {o.label}
                </button>
              ))}
            </div>
          )}

          {question.kind === "multi" && (
            <div className="flex flex-col gap-2">
              {question.options.map((o) => (
                <label key={o.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    disabled={locked}
                    checked={selected.includes(o.id)}
                    onChange={(e) =>
                      setSelected((s) => (e.target.checked ? [...s, o.id] : s.filter((x) => x !== o.id)))
                    }
                  />
                  {o.label}
                </label>
              ))}
              <button
                disabled={locked || selected.length === 0}
                onClick={() => submit(selected)}
                className="mt-1 self-start rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-on-accent disabled:opacity-40"
              >
                Submit
              </button>
            </div>
          )}

          {question.kind === "text" && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (text.trim()) submit(text.trim());
              }}
              className="flex flex-col gap-2"
            >
              <textarea
                value={text}
                disabled={locked}
                onChange={(e) => setText(e.target.value)}
                rows={2}
                className="rounded-md border border-line bg-card text-ink px-3 py-2 text-sm"
                placeholder="Type your answer…"
              />
              <button
                type="submit"
                disabled={locked || !text.trim()}
                className="self-start rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-on-accent disabled:opacity-40"
              >
                Send
              </button>
            </form>
          )}

          {sending && <p className="mt-2 text-xs text-muted">Sending…</p>}
          {paused && (
            <p className="mt-2 text-xs text-muted">
              The check is paused. You can answer now; it is applied when you resume.
            </p>
          )}
          {error && <p className="mt-2 text-xs text-bad">Couldn’t send: {error}. Try again.</p>}
        </div>
      )}
    </section>
  );
}
