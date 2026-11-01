"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { ThemeToggle } from "@/components/ThemeToggle";
import { api, Check } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [starting, setStarting] = useState(false);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listChecks()
      .then(setChecks)
      .catch((e: Error) => setError(`Couldn’t load checks: ${e.message}`));
  }, []);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim() || starting) return;
    setStarting(true);
    setError(null);
    try {
      const check = await api.createCheck(query.trim());
      router.push(`/checks/${check.id}`);
    } catch (err) {
      setError(`Couldn’t start the check: ${(err as Error).message}`);
      setStarting(false);
    }
  }

  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-4">
        <span className="grid h-8 w-8 place-items-center rounded-md bg-accent font-mono text-xs font-bold text-on-accent">
          CC
        </span>
        <span className="font-mono text-sm font-semibold">company-check</span>
        <span className="ml-auto">
          <ThemeToggle />
        </span>
      </header>

      <main className="mx-auto max-w-3xl px-4 pb-16 pt-10">
        <span className="inline-flex items-center gap-2 rounded border border-line bg-card px-2.5 py-1 text-xs text-muted">
          <span className="h-2 w-2 rounded-sm bg-accent" />
          Due diligence with a human in the loop
        </span>
        <h1 className="mt-4 font-mono text-4xl font-bold tracking-tighter sm:text-5xl">
          Check a company, <span className="text-accent">live</span>.
        </h1>
        <p className="mt-3 max-w-xl text-muted">
          The agent reads the records, reviews and news while you watch. It asks when something is
          unclear, and every piece of feedback you give changes only what it affects.
        </p>

        <form onSubmit={start} className="mt-8 flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Company name, e.g. Pinecrest Home Services"
            aria-label="Company name"
            className="min-w-0 flex-1 rounded-md border border-line bg-card px-3 py-2.5 text-sm text-ink outline-none placeholder:text-muted focus:border-accent"
            autoFocus
          />
          <button
            disabled={starting || !query.trim()}
            className="shrink-0 rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-on-accent disabled:opacity-40"
          >
            {starting ? "Starting…" : "Start check"}
          </button>
        </form>
        {error && <p className="mt-3 text-sm text-bad">{error}</p>}

        <section className="mt-12 rounded-md border border-line bg-card">
          <h2 className="label border-b border-line px-4 py-2.5">Recent checks</h2>
          {checks === null && !error && <p className="p-4 text-sm text-muted">Loading…</p>}
          {checks?.length === 0 && <p className="p-4 text-sm text-muted">No checks yet. Start one above.</p>}
          <ul className="divide-y divide-line">
            {checks?.map((c) => (
              <li key={c.id}>
                <Link
                  href={`/checks/${c.id}`}
                  className="flex items-center gap-3 px-4 py-3 text-sm hover:bg-chip/60"
                >
                  <span className="min-w-0 flex-1 truncate font-medium text-ink">{c.input}</span>
                  <span className="hidden shrink-0 font-mono text-[11px] text-muted sm:inline">
                    {new Date(c.created_at).toLocaleString(undefined, {
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                  <StatusBadge status={c.status} />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  );
}
