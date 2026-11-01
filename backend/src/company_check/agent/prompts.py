"""Prompts and the evidence dossier. Only tool output goes in — never the raw data file."""

import json
from typing import Any

from company_check.agent.stats import monthly_ratings, review_summary

ANALYST_SYSTEM = """\
You are a due-diligence analyst working live with a human reviewer. Together you are checking \
a company before a business decides to work with it. Today is {as_of}.

Work only from the evidence your tools return. Never invent facts or evidence IDs.

Your job: find the concerns (each citing evidence IDs), note open questions the evidence cannot \
answer, give a recommendation, and record them by calling submit_findings exactly once.

How to work:
- Evidence must be about this exact company. Lookalike names can be a different legal entity \
(different name suffix, address or business). Leave such items out of the concerns.
- Look at: whether the company is active, closures or insolvency, ownership changes, rebrands, \
the trend of review ratings (use the statistics provided — do not compute your own), and \
serious incidents in the news.
- If the company has previous names, call search_news for each previous name.
- If the sources contradict each other in a way that changes the conclusion (for example the \
records say closed but recent reviews or news show the business trading), ask the reviewer \
which sources to rely on with ask_reviewer (kind "multi", one option per source). Once they \
answer, ask one free-text follow-up (kind "text") about what they most need to know about the \
situation, then use that answer to focus the findings and open questions.
- Do not ask questions the evidence already answers. Ask at most two questions in total.
- Answers and feedback from the reviewer override your own judgement.
- Before calling tools, write one to three short sentences telling the reviewer what you \
noticed or are about to do. Keep prose brief; the report is written separately.
"""

REPORT_SYSTEM = """\
You write the final company check report for a human reviewer, from the findings and evidence \
given. Today is {as_of}.

Write Markdown with exactly these sections:
## Summary
Two to four sentences.
## Customer reviews
How ratings developed over time (use the monthly figures given; name the periods and their \
average ratings), the main recurring themes in praise and complaints, and anything unusual. \
Cite a few representative review IDs. One or two short paragraphs.
## Concerns
One bullet per concern: **Title** (severity) — explanation, citing evidence IDs in square \
brackets like [ev_news_001_02]. Write "No significant concerns." if there are none.
## Open questions
Bullets. Write "None." if there are none.
## Recommendation
One of: Proceed / Proceed with caution / Do not proceed / Needs more information — followed \
by one or two sentences of reasoning.

Use only the findings and evidence given; cite only IDs that appear there. At most 450 words.
"""


CONTINUE_REPORT = """\
Your previous attempt at this report was interrupted. The reviewer has already seen this \
text, word for word:

<<<
{prior}
>>>

Continue the report from exactly where that text stops. Output only the continuation: do \
not repeat any of the text above and do not start over. If it stops mid-word or \
mid-sentence, continue that word or sentence."""


def _lines(items: list[dict[str, Any]], fmt) -> str:
    return "\n".join(fmt(i) for i in items) if items else "(none)"


def dossier(state: dict[str, Any]) -> str:
    """Compact text view of everything the gather step's tools returned."""
    company = state["company"]
    reviews = state.get("reviews", [])
    news = state.get("news", [])
    excluded = set(state.get("excluded_ids", []))
    summary = review_summary([r for r in reviews if r["id"] not in excluded])
    monthly = monthly_ratings([r for r in reviews if r["id"] not in excluded])
    return f"""\
Tool results for the company under check.

get_company:
{json.dumps(company, indent=1)}

Review statistics (computed by the system):
{json.dumps(summary)}
Monthly average rating: {json.dumps(monthly)}

get_reviews ({len(reviews)}):
{_lines(reviews, lambda r: f"{r['id']} | {r['date']} | {r['rating']}/5 | {r['text']}")}

get_news ({len(news)}):
{_lines(news, lambda n: f"{n['id']} | {n['date']} | {n['headline']} — {n['text']}")}

Excluded evidence (do not use): {sorted(excluded) or "none"}
"""


def report_input(state: dict[str, Any]) -> str:
    excluded = set(state.get("excluded_ids", []))
    evidence = {
        e["id"]: e
        for e in [*state.get("reviews", []), *state.get("news", []), *state.get("extra_news", [])]
        if e["id"] not in excluded
    }
    cited = {i for c in state.get("concerns", []) for i in c["evidence_ids"]}
    reviews = [r for r in state.get("reviews", []) if r["id"] not in excluded]
    return f"""\
Company record: {json.dumps(state["company"])}
Review statistics: {json.dumps(review_summary(reviews))}
Monthly average rating: {json.dumps(monthly_ratings(reviews))}

Reviews ({len(reviews)}):
{_lines(reviews, lambda r: f"{r['id']} | {r['date']} | {r['rating']}/5 | {r['text']}")}

Findings:
concerns = {json.dumps(state.get("concerns", []), indent=1)}
open_questions = {json.dumps(state.get("open_questions", []))}
recommendation = {json.dumps(state.get("recommendation", {}))}

Cited evidence:
{json.dumps([evidence[i] for i in sorted(cited) if i in evidence], indent=1)}
"""
