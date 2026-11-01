"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Health = { database: string; temporal: string };

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-2xl font-semibold">Live Company Check</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Backend:{" "}
        {error ? (
          <span className="text-red-600">unreachable ({error})</span>
        ) : health ? (
          <span className="text-green-700">
            database {health.database}, temporal {health.temporal}
          </span>
        ) : (
          "checking…"
        )}
      </p>
    </main>
  );
}
