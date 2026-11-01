const STYLES: Record<string, { label: string; cls: string }> = {
  starting: { label: "Starting", cls: "bg-zinc-100 text-zinc-600" },
  running: { label: "Working", cls: "bg-blue-50 text-blue-700" },
  waiting_for_input: { label: "Waiting for you", cls: "bg-amber-50 text-amber-800" },
  paused: { label: "Paused", cls: "bg-zinc-200 text-zinc-700" },
  completed: { label: "Complete", cls: "bg-emerald-50 text-emerald-700" },
  error: { label: "Error", cls: "bg-red-50 text-red-700" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STYLES[status] ?? { label: status, cls: "bg-zinc-100 text-zinc-600" };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${s.cls}`}>
      {status === "running" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />}
      {s.label}
    </span>
  );
}
