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
- Show the reviewer charts when they make a pattern easier to see. If the review statistics \
report a rating shift, create a chart_rating_trend and mark the dated news event that best \
explains it, if there is one. Use chart_news_timeline when several dated news events matter, \
table_rating_periods to compare before and after an event, and table_compare_companies when \
lookalike companies could be confused. At most two charts unless the reviewer asks for more.
- To change a chart, call the same tool with its chart_id; fields you leave out keep their \
previous values.
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


FEEDBACK_SYSTEM = """\
You are a due-diligence analyst. The company check below is finished and the human reviewer \
has sent feedback. Today is {as_of}.

Act on the feedback with the tools, changing only what it affects. Everything else — other \
concerns, charts, evidence — stays as it is. Never invent facts or evidence IDs.

- Evidence that is about another company, or that the reviewer wants ignored: \
exclude_evidence. It removes concerns left without evidence and redraws the charts; then use \
update_findings only if a remaining concern or the recommendation must change.
- A change to a chart: call that chart's tool with its chart_id (fields you leave out keep \
their values). A new chart or table: call the tool without a chart_id. To mark an event, use \
its date from the evidence.
- A question about the investigation (for example a previous name): investigate with \
search_news, then update_findings if it adds or changes a concern. If it changes nothing, \
say so.
- A request about the report's wording or structure: revise_report.
- The reviewer meant a different company: switch_company with its id from the similar-names \
list, and nothing else.
- Ask the reviewer (ask_reviewer) only if the feedback is genuinely ambiguous.
- Feedback from the reviewer overrides your own judgement.

When you are done, reply to the reviewer in one to three sentences saying what you changed \
(or why nothing needed to change), without calling any tools. That reply ends the round.
"""

REVISE_REPORT = """\
This is the current report the reviewer has read:

<<<
{previous}
>>>

Since it was written: {changes}.
{instructions}
Write the full updated report. Keep every part these changes do not affect as it is, word \
for word where possible; change only what they affect."""


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
    extra = state.get("extra_news", [])
    excluded = set(state.get("excluded_ids", []))
    others = [
        {k: c[k] for k in ("id", "name", "status", "address")}
        for c in state.get("candidates", [])
        if c["id"] != company["id"]
    ]
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
{
        f'''
Found earlier with search_news ({len(extra)}):
{_lines(extra, lambda n: f"{n['id']} | {n['date']} | {n['headline']} — {n['text']}")}
'''
        if extra
        else ""
    }
Similar names (from search_companies): {json.dumps(others) if others else "none"}

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


def _norm_value(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def round_context(state: dict[str, Any], feedback: dict[str, Any]) -> str:
    """What a feedback round needs besides the dossier: current findings, charts, earlier
    answers and feedback. Built from state, so the context stays small however many rounds
    there have been."""
    charts = state.get("charts", {})
    chart_lines = [
        f"{cid}: {c['tool']} “{c['title']}” v{c['version']} params={_norm_value(c['params'])}"
        for cid, c in charts.items()
    ]
    log = [
        f"- “{f['text']}” → {'; '.join(f.get('changes') or ['no change'])}"
        for f in state.get("feedback_log", [])[-8:]
        if f.get("outcome") == "applied"
    ]
    target = feedback.get("target")
    about = f" (about {target['kind']} {target.get('id') or ''}".rstrip() + ")" if target else ""
    return f"""\
Current findings:
concerns = {json.dumps(state.get("concerns", []), indent=1)}
open_questions = {_norm_value(state.get("open_questions", []))}
recommendation = {_norm_value(state.get("recommendation", {}))}

Charts shown: {chr(10).join(chart_lines) if chart_lines else "none"}

Reviewer's earlier answers: {chr(10).join(state.get("reviewer_notes", [])) or "none"}

Earlier feedback (oldest first):
{chr(10).join(log) if log else "none"}

New feedback from the reviewer{about}:
“{feedback["text"]}”
"""
