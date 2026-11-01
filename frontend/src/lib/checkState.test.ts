import { describe, expect, it } from "vitest";
import { CheckEvent, initialView, progress, reduce } from "./checkState";

// A short but complete check: tools, a streamed note, a chart updated by feedback, findings,
// a report that was interrupted and restarted, and a repeated feedback.
const RAW: [string, Record<string, unknown>][] = [
  ["status", { status: "running" }],
  ["tool", { id: "t1", name: "search_companies", status: "started" }],
  ["tool", { id: "t1", name: "search_companies", status: "finished", summary: "1 match(es)" }],
  ["company_selected", { company_id: "cmp_6", name: "Pinecrest", status: "active" }],
  ["tool", { id: "t2", name: "get_news", status: "started" }],
  ["tool", { id: "t2", name: "get_news", status: "finished" }],
  ["message_start", { message_id: "analyst:5" }],
  ["message_delta", { message_id: "analyst:5", text: "Ratings fell " }],
  ["message_delta", { message_id: "analyst:5", text: "after the sale." }],
  ["message_end", { message_id: "analyst:5" }],
  ["chart", { chart_id: "chart-1", version: 1, tool: "chart_rating_trend", title: "Ratings", spec: {} }],
  ["findings", { concerns: [], open_questions: [], recommendation: { decision: "proceed", reason: "" } }],
  ["message_start", { message_id: "report:9" }],
  ["message_delta", { message_id: "report:9", text: "## Summ" }],
  // the worker died; the retry restarts the message
  ["message_start", { message_id: "report:9" }],
  ["message_delta", { message_id: "report:9", text: "## Summary\nAll good." }],
  ["message_end", { message_id: "report:9" }],
  ["report", { message_id: "report:9", markdown: "## Summary\nAll good.", previous: null }],
  ["status", { status: "completed" }],
  ["feedback", { id: "f1", text: "Last 12 months", target: { kind: "chart", id: "chart-1" }, queued: false }],
  ["status", { status: "running" }],
  ["chart", { chart_id: "chart-1", version: 2, tool: "chart_rating_trend", title: "Ratings", spec: {} }],
  ["feedback_result", { feedback_id: "f1", outcome: "applied", changes: ["updated chart-1"] }],
  ["status", { status: "completed" }],
  ["feedback", { id: "f2", text: "Last 12 months", target: null, queued: false }],
  ["feedback_result", { feedback_id: "f2", outcome: "duplicate", changes: [], message: "Already applied" }],
  ["usage", { message_id: "analyst:5", model: "gemini-3.5-flash-lite", input_tokens: 1000, output_tokens: 200 }],
];
const EVENTS: CheckEvent[] = RAW.map(([type, payload], i) => ({ seq: i + 1, type, payload, ts: "" }));

const fold = (events: CheckEvent[], from = initialView) => events.reduce(reduce, from);

describe("check view reducer", () => {
  it("gives the same view after a reconnect that replays already-seen events", () => {
    const full = fold(EVENTS);
    // Live until event 15, then EventSource reconnects and the server replays from 10.
    const live = fold(EVENTS.slice(0, 15));
    const reconnected = fold(EVENTS.slice(9), live);
    expect(reconnected).toEqual(full);
  });

  it("gives the same view on a full replay after refresh, event by event or at once", () => {
    let v = initialView;
    for (const e of EVENTS) v = reduce(v, e);
    expect(v).toEqual(fold(EVENTS));
  });

  it("drops half-streamed text when a message restarts, so nothing is shown twice", () => {
    const v = fold(EVENTS);
    expect(v.messages["report:9"].text).toBe("## Summary\nAll good.");
    expect(v.items.filter((i) => i.kind === "message" && i.id === "report:9")).toHaveLength(1);
  });

  it("keeps every chart version in the stream but only the latest in the case file", () => {
    const v = fold(EVENTS);
    expect(v.items.filter((i) => i.kind === "chart")).toHaveLength(2);
    expect(v.charts["chart-1"].version).toBe(2);
  });

  it("attaches round results to the feedback and doesn't repeat 'Check complete'", () => {
    const v = fold(EVENTS);
    expect(v.feedback.f1.result?.outcome).toBe("applied");
    expect(v.feedback.f2.result?.outcome).toBe("duplicate");
    const markers = v.items.filter((i) => i.kind === "marker" && i.text === "Check complete");
    expect(markers).toHaveLength(1);
  });

  it("tracks progress and token usage", () => {
    const v = fold(EVENTS);
    expect(progress(v).every((s) => s.state === "done")).toBe(true);
    expect(v.usage).toMatchObject({ calls: 1, input: 1000, output: 200, models: ["gemini-3.5-flash-lite"] });
    expect(v.usage.cost).toBeCloseTo(0.0008); // 1000 × $0.30/M + 200 × $2.50/M
    const midway = fold(EVENTS.slice(0, 6));
    expect(progress(midway).map((s) => s.state)).toEqual(["done", "done", "active", "todo"]);
  });
});
