"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
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
    <main className="mx-auto max-w-2xl px-4 py-12">
      <h1 className="text-2xl font-semibold">Live Company Check</h1>
      <p className="mt-1 text-sm text-zinc-500">
        Enter a company name. The agent investigates while you watch, answer its questions and
        give feedback.
      </p>

      <form onSubmit={start} className="mt-6 flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. Pinecrest Home Services"
          className="flex-1 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-zinc-500"
          autoFocus
        />
        <button
          disabled={starting || !query.trim()}
          className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          {starting ? "Starting…" : "Start check"}
        </button>
      </form>
      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      <h2 className="mt-10 text-sm font-semibold text-zinc-700">Recent checks</h2>
      <div className="mt-2 divide-y divide-zinc-100 rounded-xl border border-zinc-200 bg-white">
        {checks === null && !error && <p className="p-4 text-sm text-zinc-400">Loading…</p>}
        {checks?.length === 0 && <p className="p-4 text-sm text-zinc-400">No checks yet.</p>}
        {checks?.map((c) => (
          <Link
            key={c.id}
            href={`/checks/${c.id}`}
            className="flex items-center justify-between px-4 py-3 text-sm hover:bg-zinc-50"
          >
            <span className="font-medium">{c.input}</span>
            <span className="flex items-center gap-3">
              <span className="text-xs text-zinc-400">{new Date(c.created_at).toLocaleString()}</span>
              <StatusBadge status={c.status} />
            </span>
          </Link>
        ))}
      </div>
    </main>
  );
}
