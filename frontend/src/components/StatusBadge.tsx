const STYLES: Record<string, { label: string; cls: string; dot?: string }> = {
  starting: { label: "Starting", cls: "bg-chip text-muted" },
  running: { label: "Working", cls: "bg-accent/15 text-accent", dot: "bg-accent animate-pulse" },
  waiting_for_input: { label: "Waiting for you", cls: "bg-warn/15 text-warn", dot: "bg-warn" },
  paused: { label: "Paused", cls: "bg-chip text-ink" },
  completed: { label: "Complete", cls: "bg-ok/15 text-ok" },
  error: { label: "Error", cls: "bg-bad/15 text-bad" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STYLES[status] ?? { label: status, cls: "bg-chip text-muted" };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wide ${s.cls}`}
    >
      {s.dot && <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />}
      {s.label}
    </span>
  );
}
