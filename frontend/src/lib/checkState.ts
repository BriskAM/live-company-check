/**
 * The check page is a pure fold over the server's event log.
 *
 * Live updates and a full replay after a refresh go through the same reducer, so what the
 * reviewer sees after a refresh is exactly what they saw before — no gaps, no repeats.
 */

export type CheckEvent = {
  seq: number;
  type: string;
  // Shape depends on `type`; the reducer narrows it per case.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: Record<string, any>;
  ts: string;
};

export type Option = { id: string; label: string };

export type Question = {
  question_id: string;
  kind: "single" | "multi" | "text";
  question: string;
  options: Option[];
  answer?: unknown;
};

export type Tool = {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  status: "started" | "finished";
  summary?: string;
};

export type Message = {
  id: string;
  text: string;
  done: boolean;
  kind: "agent" | "report";
  previous?: string | null; // report text before this revision
};

export type Concern = {
  id: string;
  title: string;
  detail: string;
  severity: "low" | "medium" | "high";
  evidence_ids: string[];
};

export type Findings = {
  concerns: Concern[];
  open_questions: string[];
  recommendation: { decision: string; reason: string };
  changes?: string[]; // set when a feedback round patched the findings
};

export type Feedback = {
  id: string;
  text: string;
  target: { kind: string; id?: string | null } | null;
  queued: boolean;
  result?: { outcome: "applied" | "duplicate"; changes: string[]; message?: string };
};

export type RatingTrendSpec = {
  type: "rating_trend";
  range: { start: string | null; end: string | null };
  data: { month: string; avg: number; count: number }[];
  markers: { date: string; month: string; label: string }[];
  review_count: number;
};

export type NewsTimelineSpec = {
  type: "news_timeline";
  events: { id: string; date: string; headline: string; text: string }[];
  excluded: string[];
};

export type TableSpec = {
  type: "table";
  columns: { key: string; label: string; align?: "right" }[];
  rows: Record<string, unknown>[];
};

export type ChartSpec = RatingTrendSpec | NewsTimelineSpec | TableSpec;

export type Chart = {
  chart_id: string;
  version: number;
  tool: string;
  title: string;
  spec: ChartSpec;
};

export type Company = {
  company_id: string;
  name: string;
  status?: string;
  founded?: string;
  closed_on?: string;
  address?: string;
  previous_names?: string[];
};

export type Item =
  | { kind: "tool"; id: string }
  | { kind: "message"; id: string }
  | { kind: "question"; id: string }
  | { kind: "findings"; seq: number; findings: Findings }
  | { kind: "chart"; seq: number; chart: Chart }
  | { kind: "company"; seq: number; name: string }
  | { kind: "feedback"; id: string }
  | { kind: "excluded"; seq: number; ids: string[]; reason: string }
  | { kind: "marker"; seq: number; text: string; tone: "info" | "warn" | "error" };

export type CheckView = {
  lastSeq: number;
  status: string;
  error?: string;
  errorDetail?: string;
  items: Item[];
  tools: Record<string, Tool>;
  messages: Record<string, Message>;
  questions: Record<string, Question>;
  latestChart: Record<string, number>; // chart_id -> latest version seen
  feedback: Record<string, Feedback>;
  latestFindings: number; // seq of the newest findings
  latestReport: string | null; // message id of the newest report
  // Current state for the case file (the stream keeps the history).
  company: Company | null;
  findings: Findings | null;
  charts: Record<string, Chart>; // latest version of each chart, in creation order
  completedOnce: boolean; // later runs are feedback rounds
  usage: { calls: number; input: number; output: number; models: string[] };
};

export const initialView: CheckView = {
  lastSeq: 0,
  status: "starting",
  items: [],
  tools: {},
  messages: {},
  questions: {},
  latestChart: {},
  feedback: {},
  latestFindings: 0,
  latestReport: null,
  company: null,
  findings: null,
  charts: {},
  completedOnce: false,
  usage: { calls: 0, input: 0, output: 0, models: [] },
};

const STATUS_MARKERS: Record<string, { text: string; tone: "info" | "warn" | "error" }> = {
  paused: { text: "Paused by reviewer", tone: "warn" },
  completed: { text: "Check complete", tone: "info" },
  error: { text: "The agent hit an error", tone: "error" },
};

export function reduce(view: CheckView, e: CheckEvent): CheckView {
  if (e.seq <= view.lastSeq) return view; // already applied (reconnect overlap)
  const v: CheckView = { ...view, lastSeq: e.seq };
  const p = e.payload;

  switch (e.type) {
    case "status": {
      const wasPaused = view.status === "paused";
      v.status = p.status;
      v.error = p.status === "error" ? p.message : undefined;
      v.errorDetail = p.status === "error" ? p.detail : undefined;
      // After the first completion, each feedback round ends with its own "Applied: …" note,
      // so another "Check complete" line would only repeat it.
      const repeat = p.status === "completed" && view.completedOnce;
      const marker = repeat ? undefined : STATUS_MARKERS[p.status];
      if (p.status === "completed") v.completedOnce = true;
      if (marker) v.items = [...v.items, { kind: "marker", seq: e.seq, ...marker }];
      else if (wasPaused && p.status !== "paused")
        v.items = [...v.items, { kind: "marker", seq: e.seq, text: "Resumed", tone: "info" }];
      break;
    }
    case "tool": {
      const prev = view.tools[p.id];
      v.tools = { ...view.tools, [p.id]: { ...prev, ...p } as Tool };
      if (!prev) v.items = [...v.items, { kind: "tool", id: p.id }];
      break;
    }
    case "message_start": {
      // Also sent when a step is retried: discard any half-streamed text for this id.
      const prev = view.messages[p.message_id];
      v.messages = {
        ...view.messages,
        [p.message_id]: { id: p.message_id, text: "", done: false, kind: prev?.kind ?? kindOf(p.message_id) },
      };
      if (!prev) v.items = [...v.items, { kind: "message", id: p.message_id }];
      if (kindOf(p.message_id) === "report") v.latestReport = p.message_id;
      break;
    }
    case "message_delta": {
      const m = view.messages[p.message_id] ?? {
        id: p.message_id,
        text: "",
        done: false,
        kind: kindOf(p.message_id),
      };
      if (!view.messages[p.message_id]) v.items = [...v.items, { kind: "message", id: p.message_id }];
      if (m.kind === "report") v.latestReport = p.message_id;
      v.messages = { ...view.messages, [p.message_id]: { ...m, text: m.text + p.text } };
      break;
    }
    case "message_end": {
      const m = view.messages[p.message_id];
      if (m) v.messages = { ...view.messages, [p.message_id]: { ...m, done: true } };
      break;
    }
    case "report": {
      // Authoritative full text of a finished report.
      const m = view.messages[p.message_id];
      if (!m) v.items = [...v.items, { kind: "message", id: p.message_id }];
      v.messages = {
        ...view.messages,
        [p.message_id]: { id: p.message_id, text: p.markdown, done: true, kind: "report", previous: p.previous },
      };
      v.latestReport = p.message_id;
      break;
    }
    case "question": {
      const prev = view.questions[p.question_id];
      v.questions = { ...view.questions, [p.question_id]: { ...(p as Question), answer: prev?.answer } };
      if (!prev) v.items = [...v.items, { kind: "question", id: p.question_id }];
      break;
    }
    case "answer": {
      const q = view.questions[p.question_id];
      if (q) v.questions = { ...view.questions, [p.question_id]: { ...q, answer: p.value } };
      break;
    }
    case "chart": {
      // Each version is its own timeline item, so an update appears where it happened;
      // the timeline collapses older versions.
      const chart = p as Chart;
      v.items = [...v.items, { kind: "chart", seq: e.seq, chart }];
      v.latestChart = { ...view.latestChart, [chart.chart_id]: chart.version };
      v.charts = { ...view.charts, [chart.chart_id]: chart };
      break;
    }
    case "findings":
      v.items = [...v.items, { kind: "findings", seq: e.seq, findings: p as Findings }];
      v.latestFindings = e.seq;
      v.findings = p as Findings;
      break;
    case "feedback":
      if (!view.feedback[p.id]) {
        v.feedback = { ...view.feedback, [p.id]: p as Feedback };
        v.items = [...v.items, { kind: "feedback", id: p.id }];
      }
      break;
    case "feedback_result": {
      const f = view.feedback[p.feedback_id];
      if (f) v.feedback = { ...view.feedback, [p.feedback_id]: { ...f, result: p as Feedback["result"] } };
      break;
    }
    case "usage":
      v.usage = {
        calls: view.usage.calls + 1,
        input: view.usage.input + (p.input_tokens ?? 0),
        output: view.usage.output + (p.output_tokens ?? 0),
        models: p.model && !view.usage.models.includes(p.model) ? [...view.usage.models, p.model] : view.usage.models,
      };
      break;
    case "notice":
      v.items = [...v.items, { kind: "marker", seq: e.seq, text: p.text, tone: "warn" }];
      break;
    case "evidence_excluded":
      v.items = [...v.items, { kind: "excluded", seq: e.seq, ids: p.ids, reason: p.reason }];
      break;
    case "company_selected":
      v.items = [...v.items, { kind: "company", seq: e.seq, name: p.name }];
      // A different company resets what the case file shows.
      if (view.company && view.company.company_id !== p.company_id) {
        v.findings = null;
        v.charts = {};
        v.latestReport = null;
      }
      v.company = p as Company;
      break;
  }
  return v;
}

function kindOf(messageId: string): Message["kind"] {
  return messageId.startsWith("report:") ? "report" : "agent";
}

export type Step = { label: string; state: "done" | "active" | "todo" };

/** The four phases of a check, for the header. */
export function progress(view: CheckView): Step[] {
  const tools = Object.values(view.tools);
  const report = view.latestReport ? view.messages[view.latestReport] : undefined;
  const done = [
    view.company !== null,
    tools.some((t) => t.name === "get_news" && t.status === "finished"),
    view.findings !== null,
    !!report?.done,
  ];
  const labels = ["Find company", "Gather data", "Analyse", "Write report"];
  const firstOpen = done.indexOf(false);
  const steps: Step[] = labels.map((label, i) => ({
    label,
    state: done[i] ? "done" : i === firstOpen ? "active" : "todo",
  }));
  if (view.completedOnce && view.status !== "completed" && firstOpen === -1)
    steps.push({ label: "Feedback round", state: "active" });
  return steps;
}
