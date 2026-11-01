# Architecture

Each concern has one owner. Temporal decides **when** the agent runs, LangGraph decides
**what** it does, Postgres stores **what the reviewer sees**, and the browser folds that
log into the UI.

The diagram covers the system, agent flow and recovery guarantees.

![System architecture and agent flow](diagrams/architecture.svg)

## Components

```mermaid
flowchart LR
    subgraph Browser
        UI["Next.js UI<br/>reducer over events"]
    end

    subgraph API["FastAPI"]
        REST["REST commands<br/>create, answer, feedback,<br/>pause, resume, retry"]
        SSE["SSE stream<br/>replay + live"]
    end

    subgraph Temporal
        WF["CheckWorkflow<br/>one per check:<br/>running, waiting, paused,<br/>error, completed"]
    end

    subgraph Worker["Worker"]
        ACT["run_agent activity"]
        G["LangGraph agent"]
        ACT --> G
    end

    subgraph PG["PostgreSQL"]
        D[("companies, reviews, news")]
        E[("check_events<br/>append-only log")]
        C[("commands<br/>idempotency")]
        K[("LangGraph checkpoints")]
    end

    LLM["Gemini<br/>3.5 Flash-Lite, 3.1 Flash-Lite fallback"]

    UI -- "POST with command_id" --> REST
    REST -- "insert" --> C
    REST -- "signal" --> WF
    WF -- "schedules, cancels on pause" --> ACT
    G -- "tool calls" --> D
    G -- "checkpoint per node" --> K
    G -- "stream tokens" --> LLM
    G -- "events + NOTIFY" --> E
    WF -- "status events" --> E
    E -- "LISTEN" --> SSE
    SSE -- "EventSource,<br/>Last-Event-ID" --> UI
```

| Concern | Owner | Why |
|---|---|---|
| Control flow: running, waiting for an answer, paused, error, waiting for feedback | Temporal workflow | Survives restarts; waits for hours at no cost; signals for commands |
| Agent reasoning | LangGraph, run inside a Temporal activity | Workflow code must be deterministic; LLM and DB calls are not |
| Agent progress | LangGraph Postgres checkpointer (thread = check id) | Resume or retry continues from the last finished node |
| What the reviewer sees | `check_events` (per-check `seq`, idempotency keys) | Refresh or reconnect replays exactly what was shown |
| Domain data | Postgres tables, read only through tool functions | The LLM never sees the raw data file |

## Agent graph

```mermaid
flowchart TD
    START((start)) -->|first run| resolve
    START -->|feedback| intake[feedback_intake]
    resolve -->|ambiguous name: single-choice question| resolve
    resolve --> gather
    gather --> analyst
    analyst --> tools
    tools -->|more work| analyst
    tools -->|findings submitted / feedback changed the report| report
    tools -->|feedback round with nothing to rewrite| finish[finish_round]
    tools -->|switch_company| gather
    report --> finish
    finish --> END((end))
    intake -->|new feedback| analyst
    intake -->|repeat that cannot change anything| END
```

- **resolve** and **gather** are plain code: trigram name search, then company, reviews and
  news. An ambiguous name pauses the graph with a single-choice question (`interrupt`).
- **analyst** is one LLM call; **tools** runs the calls it asked for in a separate node, so
  a question (`interrupt`) never repeats the LLM call when the graph resumes.
- First run tools: `ask_reviewer`, `search_news`, four chart and table tools,
  `submit_findings`. Feedback-round tools: `exclude_evidence`, `update_findings`,
  `revise_report`, `switch_company`, plus the chart tools and `search_news`.
- **report** streams Markdown. After a pause or crash it continues the text the reviewer
  already saw; in a feedback round it revises the previous version.

## Workflow

```mermaid
stateDiagram-v2
    [*] --> running
    running --> waiting_for_input: agent asked a question
    waiting_for_input --> running: all questions answered
    running --> paused: pause (activity cancelled, text saved)
    waiting_for_input --> paused: pause
    paused --> running: resume (continue from checkpoint)
    running --> error: failed after retries
    error --> running: retry
    running --> completed
    completed --> running: feedback (queued while busy)
    completed --> completed: continue-as-new every 8 rounds
```

## A reviewer command, end to end

```mermaid
sequenceDiagram
    participant UI
    participant API
    participant WF as Workflow
    participant A as run_agent
    participant DB as Postgres

    UI->>API: POST /checks/{id}/feedback (command_id)
    API->>DB: insert commands (dupe ignored)
    API->>WF: signal feedback
    WF->>DB: event "feedback" (key feedback:{command_id})
    WF->>A: run_agent(feedback)
    A->>DB: read checkpoint, decide how to enter the graph
    loop each node
        A->>DB: events (tools, text deltas every 100 ms, charts, findings)
        DB-->>UI: NOTIFY, SSE
        A->>DB: checkpoint
    end
    A-->>WF: done
    WF->>DB: event status completed
```

Pause cancels the activity through its heartbeat; it saves streamed text and stops.
Resume runs it again and it continues from the last checkpoint. Every command carries a
client-generated `command_id`, deduplicated by the API and again in the workflow.

## Failures

| Failure | What happens |
|---|---|
| LLM overload (503), rate limit (429), timeout | Model fallback, then Temporal retries with backoff; the UI shows "Recovering from a temporary problem" |
| Bad API key or billing (401/402/403) | Fails fast (not retried); the check shows a plain message and a Retry button |
| Agent doesn't finish within 8 turns | It is told to finish now; only if that fails does the check go to error |
| Malformed tool call | The validation error goes back to the model to fix |
| Worker killed mid-stream | Heartbeat timeout, then a retry continues from the checkpoint; a half-written report is continued, not repeated |
| Worker stopped (`docker stop`) | Graceful shutdown: running agents get 5 s to finish or save progress |
| API restarted | Stateless; browsers reconnect with `Last-Event-ID` |
| Temporal restarted | Workflow state is in its SQLite file; paused and waiting checks survive |
