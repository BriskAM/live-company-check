export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Check = {
  id: string;
  input: string;
  company_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${detail || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listChecks: () => request<Check[]>("/checks"),
  getCheck: (id: string) => request<Check>(`/checks/${id}`),
  createCheck: (query: string) =>
    request<Check>("/checks", { method: "POST", body: JSON.stringify({ query }) }),

  /**
   * Reviewer commands. The caller passes a command id created once per user action, so
   * retrying the same action (network blip, double click) is recognised as a duplicate.
   */
  answer: (id: string, commandId: string, questionId: string, value: unknown) =>
    request(`/checks/${id}/answers`, {
      method: "POST",
      body: JSON.stringify({ command_id: commandId, question_id: questionId, value }),
    }),
  control: (id: string, action: "pause" | "resume" | "retry", commandId: string) =>
    request(`/checks/${id}/${action}`, {
      method: "POST",
      body: JSON.stringify({ command_id: commandId }),
    }),
};

export const newCommandId = () => crypto.randomUUID();
