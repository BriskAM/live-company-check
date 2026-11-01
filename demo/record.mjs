// Records the demo: drives the real app in Chrome through four scenes, with captions and a
// visible cursor, capturing frames over CDP. `node build.mjs` turns the frames into a video,
// speeding up the stretches where the agent is working.
//
//   node record.mjs            needs the API, worker and UI running (see docs/DEMO.md)
//
// Kills and restarts the local worker once, to show recovery mid-report. Clears all checks first.

import { execSync, spawn } from "node:child_process";
import { mkdirSync, openSync, rmSync, writeFileSync } from "node:fs";
import { chromium } from "playwright-core";

const APP = "http://localhost:3000";
const API = "http://localhost:8000";
const BACKEND = new URL("../backend/", import.meta.url).pathname;
const OUT = new URL("./out/", import.meta.url).pathname;
const WORKER_LOG = `${OUT}worker.log`;
const LONG = 6 * 60_000;

rmSync(OUT, { recursive: true, force: true });
mkdirSync(`${OUT}frames`, { recursive: true });

// --- overlay: caption card + cursor, injected into every page -------------------------

const OVERLAY = `(() => {
  const css = \`
    #demo-cursor{position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;pointer-events:none;
      transform:translate(-3px,-2px);transition:transform .05s linear;filter:drop-shadow(0 1px 2px rgba(0,0,0,.5))}
    #demo-ripple{position:fixed;width:36px;height:36px;margin:-18px 0 0 -18px;border-radius:50%;z-index:2147483646;
      pointer-events:none;border:2px solid #bd93f9;opacity:0}
    #demo-ripple.on{animation:demo-ripple .45s ease-out}
    @keyframes demo-ripple{from{opacity:1;transform:scale(.4)}to{opacity:0;transform:scale(1.4)}}
    #demo-caption{position:fixed;right:24px;bottom:24px;max-width:500px;z-index:2147483645;pointer-events:none;
      background:rgba(24,25,33,.94);color:#f8f8f2;border:1px solid #44475a;border-left:3px solid #bd93f9;
      border-radius:8px;padding:12px 16px;font:15px/1.45 ui-sans-serif,system-ui,-apple-system,sans-serif;
      box-shadow:0 8px 30px rgba(0,0,0,.35);transition:opacity .25s}
    nextjs-portal{display:none!important}
    #demo-caption .ch{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.08em;
      text-transform:uppercase;color:#bd93f9;margin-bottom:6px}
  \`;
  function mount() {
    if (document.getElementById("demo-cursor")) return;
    const style = document.createElement("style"); style.textContent = css; document.head.appendChild(style);
    const cur = document.createElement("div"); cur.id = "demo-cursor";
    cur.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M4 2l16 10-7 1.5L9.5 21z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    const rip = document.createElement("div"); rip.id = "demo-ripple";
    const cap = document.createElement("div"); cap.id = "demo-caption"; cap.style.opacity = "0";
    document.body.append(cur, rip, cap);
    const pos = JSON.parse(sessionStorage.getItem("demo-pos") || "[720,450]");
    cur.style.transform = "translate(" + (pos[0]-3) + "px," + (pos[1]-2) + "px)";
    document.addEventListener("mousemove", (e) => {
      cur.style.transform = "translate(" + (e.clientX-3) + "px," + (e.clientY-2) + "px)";
      sessionStorage.setItem("demo-pos", JSON.stringify([e.clientX, e.clientY]));
    }, true);
    document.addEventListener("mousedown", (e) => {
      rip.style.left = e.clientX + "px"; rip.style.top = e.clientY + "px";
      rip.classList.remove("on"); void rip.offsetWidth; rip.classList.add("on");
    }, true);
    window.__caption = (text, chapter) => {
      sessionStorage.setItem("demo-caption", JSON.stringify([text, chapter]));
      if (!text) { cap.style.opacity = "0"; return; }
      cap.innerHTML = (chapter ? '<div class="ch"></div>' : "") + "<div class=tx></div>";
      if (chapter) cap.querySelector(".ch").textContent = chapter;
      cap.querySelector(".tx").textContent = text;
      cap.style.opacity = "1";
    };
    const saved = JSON.parse(sessionStorage.getItem("demo-caption") || "null");
    if (saved) window.__caption(saved[0], saved[1]);
  }
  try { localStorage.setItem("theme", "dark"); } catch (e) {}
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();`;

// --- slides ---------------------------------------------------------------------------

const SLIDE_CSS = `
  *{box-sizing:border-box;margin:0}
  body{background:#21222c;color:#f8f8f2;font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;
    height:100vh;display:flex;align-items:center;justify-content:center}
  .wrap{width:1080px}
  .tag{display:inline-block;font:600 13px ui-monospace,Menlo,monospace;letter-spacing:.08em;text-transform:uppercase;
    color:#bd93f9;border:1px solid #44475a;border-radius:999px;padding:6px 12px;margin-bottom:28px}
  h1{font:800 72px/1.05 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:-.03em}
  h1 em{font-style:normal;color:#bd93f9}
  h2{font:700 40px/1.1 ui-monospace,Menlo,monospace;letter-spacing:-.02em;margin-bottom:28px}
  p.lead{font-size:24px;line-height:1.5;color:#c6c7d1;margin-top:24px;max-width:900px}
  ol{margin-top:40px;padding:0;list-style:none;display:grid;grid-template-columns:1fr 1fr;gap:14px 40px}
  ol li{font-size:20px;color:#e6e6ee;display:flex;gap:14px;align-items:baseline}
  ol li b{font:700 14px ui-monospace,Menlo,monospace;color:#bd93f9}
  .flow{display:flex;align-items:stretch;gap:14px;margin:8px 0 22px}
  .box{flex:1;background:#2b2d3a;border:1px solid #44475a;border-radius:10px;padding:16px 18px}
  .box .k{font:700 17px ui-monospace,Menlo,monospace;color:#f8f8f2;margin-bottom:6px}
  .box .v{font-size:15px;line-height:1.45;color:#a9abb8}
  .box.hl{border-color:#bd93f9}
  .arrow{align-self:center;color:#6c6f85;font-size:24px}
  .db{background:#2b2d3a;border:1px dashed #6c6f85;border-radius:10px;padding:14px 18px;font-size:16px;color:#c6c7d1}
  .db b{font-family:ui-monospace,Menlo,monospace;color:#f8f8f2}
  .stats{display:flex;gap:16px;margin-top:26px}
  .stat{flex:1;border-top:2px solid #bd93f9;padding-top:12px}
  .stat .n{font:800 34px ui-monospace,Menlo,monospace}
  .stat .l{font-size:15px;color:#a9abb8;margin-top:4px}
  .url{margin-top:30px;font:600 20px ui-monospace,Menlo,monospace;color:#bd93f9}
`;

const TITLE = `<style>${SLIDE_CSS}</style><div class="wrap">
  <div class="tag">Live Company Check</div>
  <h1>Live Company <em>Check</em></h1>
  <p class="lead">An AI agent that checks a company while a human reviewer watches, answers its questions and steers it with feedback.</p>
  <ol>
    <li><b>01</b>Ambiguous names: the agent asks</li>
    <li><b>02</b>Pause mid-sentence, survive a crash</li>
    <li><b>03</b>Multiple-choice and free-text questions</li>
    <li><b>04</b>Charts the reviewer can reshape</li>
    <li><b>05</b>Evidence feedback, selective recompute</li>
    <li><b>06</b>Repeated feedback costs nothing</li>
  </ol></div>`;

const ARCH = `<style>${SLIDE_CSS}</style><div class="wrap">
  <h2>How it fits together</h2>
  <div class="flow">
    <div class="box"><div class="k">Next.js</div><div class="v">A reducer over the event stream. Refresh and reconnect replay the same log.</div></div>
    <div class="arrow">→</div>
    <div class="box"><div class="k">FastAPI</div><div class="v">Idempotent commands in, Server-Sent Events out.</div></div>
    <div class="arrow">→</div>
    <div class="box hl"><div class="k">Temporal</div><div class="v">One workflow per check: questions, pause, retries, queued feedback, continue-as-new.</div></div>
    <div class="arrow">→</div>
    <div class="box hl"><div class="k">LangGraph</div><div class="v">The agent, checkpointed every step. Gemini Flash-Lite with a Flash fallback.</div></div>
  </div>
  <div class="db"><b>Postgres</b> · company data · append-only event log with per-check sequence and idempotency keys · LangGraph checkpoints</div>
  <div class="stats">
    <div class="stat"><div class="n">26/26</div><div class="l">sample-check expectations, driven through the API</div></div>
    <div class="stat"><div class="n">48</div><div class="l">backend and frontend tests, no LLM calls</div></div>
    <div class="stat"><div class="n">1</div><div class="l">command for the whole stack: docker compose --profile app up</div></div>
  </div>
  <div class="url">github.com/BriskAM/live-company-check</div></div>`;

// --- browser sessions -----------------------------------------------------------------
//
// On some machines Chrome is shut down ~30 s after it starts (SIGTERM from outside). The app
// replays its event log on load, so we relaunch Chrome every LIFE ms, restore the page,
// cursor, caption and scroll position, and cut the gap out of the video.

const LIFE = 24_000;
const CUT = 1e9; // speed that makes a stretch vanish

const frames = [];
const marks = [];
const now = () => Date.now() / 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let curSpeed = 1;
function speed(s) {
  curSpeed = s;
  marks.push({ t: now(), speed: s });
}

const state = { slide: null, mouse: [720, 450], caption: ["", null], show: null, diff: false };
let browser = null;
let page = null;
let born = 0;
let launches = 0;

async function launch() {
  const url = page && !state.slide ? page.url() : null;
  const resume = curSpeed;
  if (browser) {
    marks.push({ t: now(), speed: CUT });
    await browser.close().catch(() => {});
  }
  browser = await chromium.launch({ channel: "chrome", headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
  await ctx.addInitScript(OVERLAY);
  page = await ctx.newPage();
  born = Date.now();
  launches++;
  if (state.slide) {
    await page.setContent(state.slide);
  } else if (url && url.startsWith("http")) {
    await page.goto(url);
    await sleep(1500); // let the event log replay
  }
  await page.mouse.move(...state.mouse);
  await page.evaluate(([t, c]) => window.__caption?.(t, c), state.caption).catch(() => {});
  if (state.show) await page.locator(state.show).first().scrollIntoViewIfNeeded().catch(() => {});
  if (state.diff) {
    await page.getByRole("button", { name: "Highlight changes" }).click({ timeout: 2000 }).catch(() => {});
  }
  await sleep(300);
  const cdp = await ctx.newCDPSession(page);
  cdp.on("Page.screencastFrame", async ({ data, metadata, sessionId }) => {
    const file = `${OUT}frames/${String(frames.length).padStart(6, "0")}.jpg`;
    writeFileSync(file, Buffer.from(data, "base64"));
    frames.push({ file, t: metadata.timestamp });
    await cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: 2880, maxHeight: 1800 });
  await sleep(150);
  if (launches > 1) speed(resume);
}

/** Make sure the current Chrome will live at least `need` more seconds. */
async function fresh(need = 4) {
  if (!browser?.isConnected() || page.isClosed() || Date.now() - born + need * 1000 > LIFE) await launch();
}

/** Poll a condition, relaunching Chrome as needed. */
async function until(cond, { timeout = LONG, poll = 300, what = "condition" } = {}) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    await fresh(2);
    try {
      if (await cond()) return true;
    } catch {
      // the browser went away mid-check; the next round relaunches it
    }
    await sleep(poll);
  }
  throw new Error(`timed out waiting for ${what}`);
}

const visible = (text) => () => page.getByText(text).first().isVisible();

// --- actions --------------------------------------------------------------------------

async function caption(text, chapter) {
  state.caption = [text, chapter ?? null];
  await fresh(2);
  await page.evaluate(([t, c]) => window.__caption?.(t, c), state.caption).catch(() => {});
}

async function moveTo(get) {
  await fresh(3);
  const loc = get();
  await loc.scrollIntoViewIfNeeded();
  const b = await loc.boundingBox();
  if (!b) return;
  state.mouse = [b.x + b.width / 2, b.y + b.height / 2];
  await page.mouse.move(...state.mouse, { steps: 28 });
  await sleep(180);
}

async function click(get) {
  await moveTo(get);
  await get().click();
  await sleep(250);
}

async function type(get, text) {
  await fresh(4 + text.length * 0.04);
  await click(get);
  await page.keyboard.type(text, { delay: 32 });
  await sleep(300);
}

async function hold(ms) {
  // Long holds are split so Chrome can be replaced in between.
  while (ms > 0) {
    await fresh(Math.min(ms, 4000) / 1000 + 0.5);
    await sleep(Math.min(ms, 4000));
    ms -= 4000;
  }
}

async function show(selector) {
  state.show = selector;
  await fresh(3);
  await page.locator(selector).first().scrollIntoViewIfNeeded().catch(() => {});
  await sleep(500);
}

/** Wait with the clip sped up (the build caps each sped-up stretch at a few seconds). */
async function working(fn) {
  speed(8);
  try {
    return await fn();
  } finally {
    speed(1);
  }
}

let currentCheck = null;

async function status() {
  return (await (await fetch(`${API}/checks/${currentCheck}`)).json()).status;
}

async function waitStatus(want) {
  await until(
    async () => {
      const s = await status();
      if (s === "error") throw new Error("check failed");
      return want.includes(s);
    },
    { what: want.join("/"), poll: 400 },
  );
}

/** After feedback: wait until every bubble has its result and the agent is idle again. */
async function waitRound() {
  await sleep(1500);
  await until(
    () => page.evaluate(() => !/Working on it…|Queued: handled/.test(document.querySelector("main")?.innerText ?? "")),
    { what: "feedback results", poll: 500 },
  );
  await waitStatus(["completed"]);
  await sleep(800);
}

async function goHome() {
  state.slide = null;
  state.show = null;
  state.diff = false;
  await fresh(4);
  await page.goto(APP);
  await sleep(900);
}

async function startCheck(name) {
  await goHome();
  await type(() => page.getByPlaceholder(/Company name/), name);
  await click(() => page.getByRole("button", { name: "Start check" }));
  await page.waitForURL(/\/checks\//);
  currentCheck = page.url().split("/checks/")[1];
  await sleep(800);
}

const feedbackInput = () => page.locator("#feedback-input");
const sendButton = () => page.locator("form:has(#feedback-input) button[type=submit]");

async function feedback(text) {
  await type(feedbackInput, text);
  await click(sendButton);
}

/** Park the cursor on Pause and click the moment the report starts streaming. */
async function pauseWhenReportStarts() {
  await moveTo(() => page.getByRole("button", { name: "Pause" }));
  let caught = false;
  await working(() =>
    until(
      async () => {
        if (await page.getByText("Writing the report…").first().isVisible()) {
          await page.mouse.click(...state.mouse);
          caught = true;
          return true;
        }
        return (await status()) === "completed";
      },
      { what: "report", poll: 80 },
    ),
  );
  return caught;
}

async function slide(html, ms) {
  state.slide = html;
  await fresh(ms / 1000 + 1);
  await page.setContent(html);
  await sleep(ms);
}

// --- scenes ---------------------------------------------------------------------------

async function brightline() {
  const ch = "1 · Ambiguous name, pause, crash";
  await goHome();
  await caption("The reviewer types a company name. This one matches three similar companies.", ch);
  await startCheck("Brightline Logistics");
  await caption("The agent searches, sees three close matches and asks instead of guessing.", ch);
  await working(() => until(visible("Question for you"), { what: "question" }));
  await hold(1200);
  await click(() => page.getByRole("button", { name: /Brightline Logistics Ltd/ }));
  await caption("Records, reviews and news stream in on the left; the case file builds on the right.", ch);

  if (await pauseWhenReportStarts()) {
    await caption("Paused while the report was being written, mid-sentence.", ch);
    await waitStatus(["paused"]);
    await click(() => page.getByRole("button", { name: "View" }).last());
    state.show = "#cf-report";
    await hold(3500);
    await caption("Resume: the model continues the same sentence instead of starting over.", ch);
    await click(() => page.getByRole("button", { name: "Resume" }));
  } else {
    console.error("missed the pause window");
  }
  await working(() => waitStatus(["completed"]));
  await hold(2500);

  await caption("Now the worst case. Ask for a rewrite, then kill -9 the worker halfway through.", ch);
  await feedback("Rewrite the report as a short memo for a credit committee.");
  await working(() => until(visible("Writing the report…"), { what: "rewrite", poll: 100 }));
  await sleep(600);
  execSync("pkill -9 -f 'company_check[.]worker[.]main' || true"); // [.] so pkill skips its own shell
  await caption("Worker killed (kill -9) mid-report. Temporal notices and retries; the text resumes from the checkpoint.", ch);
  await hold(2500);
  const log = openSync(WORKER_LOG, "a");
  spawn("uv", ["run", "python", "-m", "company_check.worker.main"], {
    cwd: BACKEND,
    detached: true,
    stdio: ["ignore", log, log],
  }).unref();
  await working(() => until(visible(/Recovering from a temporary problem/), { what: "recovery notice" }));
  await hold(2000);
  await working(() => waitRound());
  await click(() => page.getByRole("button", { name: "View" }).last());
  await hold(2500);
  await caption("A browser refresh replays the event log and shows exactly the same page.", ch);
  await fresh(6);
  await page.reload();
  await hold(3500);
}

async function harborview() {
  const ch = "2 · Multiple choice and free text";
  await caption("Records say this caterer closed; recent reviews and news say it is still trading.", ch);
  await startCheck("Harborview Catering");
  await working(() => until(visible("Question for you"), { what: "question" }));
  await caption("The agent asks which sources to rely on, as a multiple-choice question.", ch);
  await hold(1500);
  const boxes = () => page.locator("label:has(input[type=checkbox])");
  const n = await boxes().count();
  for (let i = 0; i < n; i++) {
    const label = (await boxes().nth(i).innerText()).toLowerCase();
    if (/review|news/.test(label) && !/record/.test(label)) await click(() => boxes().nth(i));
  }
  await click(() => page.getByRole("button", { name: "Submit" }));
  const answer = () => page.getByPlaceholder("Type your answer…");
  await working(() => until(() => answer().isVisible(), { what: "free-text question" }));
  await caption("Then a free-text question about what matters to the reviewer.", ch);
  await hold(1500);
  await type(answer, "Whether the new business is a separate legal company.");
  await click(() => page.locator("form:has(textarea[placeholder='Type your answer…']) button[type=submit]"));
  await caption("The findings now focus on that question.", ch);
  await working(() => waitStatus(["completed"]));
  await show("#cf-findings");
  await hold(4000);
}

async function pinecrest() {
  const ch = "3 · Charts the reviewer can reshape";
  const CHART = '[id^="cf-chart-"]:has-text("rating")';
  await caption("Pinecrest's ratings fell after it was sold.", ch);
  await startCheck("Pinecrest Home Services");
  await working(() => waitStatus(["completed"]));
  await show(CHART);
  await caption("The agent noticed the drop and charted it without being asked.", ch);
  await hold(3500);
  await click(() => page.locator(CHART).first().getByRole("button", { name: "Feedback" }));
  await caption("Feedback can target a chart, a concern, an evidence item or the report.", ch);
  await type(feedbackInput, "Only show the last 12 months on this chart.");
  await click(sendButton);
  await working(() => waitRound());
  await show(CHART);
  await caption("Same chart, new version: only it was recomputed, nothing else.", ch);
  await hold(3500);
  await feedback("Mark the date the company was sold on the chart.");
  await working(() => waitRound());
  await show(CHART);
  await caption("Updates merge: the 12-month range stays and the sale date is marked.", ch);
  await hold(4000);
}

async function northwind() {
  const ch = "4 · Evidence feedback and repeats";
  await caption("Two news items found for Northwind are really about a dental clinic.", ch);
  await startCheck("Northwind Dental Supplies");
  await working(() => waitStatus(["completed"]));
  const text = "ev_news_004_02 and ev_news_004_04 are about the dental clinic, not this company. Leave them out.";
  const chip = () => page.locator("#cf-findings button", { hasText: "ev_news_004_02" });
  if (await chip().count()) {
    await show("#cf-findings");
    await caption("Click the evidence id to give feedback on exactly that item.", ch);
    await hold(1500);
    await click(() => chip().first());
  }
  await feedback(text);
  await caption("Exclusion is applied directly: only the concerns and charts that used it are redone.", ch);
  await working(() => waitRound());
  await hold(2500);
  const diff = () => page.getByRole("button", { name: "Highlight changes" });
  if (await diff().count()) {
    await click(diff);
    state.diff = true;
    state.show = "#cf-report";
    await caption("The revised report highlights what changed.", ch);
    await hold(3500);
  }
  const usage = () => page.getByText(/tokens · \d+ calls/);
  await moveTo(usage);
  await caption("Watch the token counter, then send the same feedback twice.", ch);
  await hold(2500);
  await feedback(text);
  await feedback(text);
  await working(() => waitRound());
  await moveTo(usage);
  await caption("Both repeats are caught before the LLM: 'already applied', and the counter didn't move.", ch);
  await hold(4500);
}

// --- run ------------------------------------------------------------------------------

try {
  execSync(`cd ${BACKEND} && uv run python scripts/reset_demo.py --yes`, { stdio: "inherit" });
  speed(1);
  await slide(TITLE, 6000);
  for (const scene of [brightline, harborview, pinecrest, northwind]) {
    try {
      console.log(`scene ${scene.name}`);
      await scene();
    } catch (e) {
      console.error(`scene ${scene.name} failed:`, e.message);
      speed(1);
      await fresh(3).catch(() => {});
      await page.screenshot({ path: `${OUT}${scene.name}-failed.png` }).catch(() => {});
    }
  }
  await caption("");
  await slide(ARCH, 9000);
} finally {
  speed(1);
  writeFileSync(`${OUT}timeline.json`, JSON.stringify({ frames, marks, end: now() }));
  await browser?.close().catch(() => {});
  console.log(`${frames.length} frames recorded, Chrome launched ${launches} times`);
}
