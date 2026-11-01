// Turns out/frames + out/timeline.json into out/demo.mp4. Stretches marked as "working" are
// sped up: 8x, and more if needed so none lasts longer than MAX_FAST seconds on screen.
// Gaps while Chrome was being relaunched are cut.

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const OUT = new URL("./out/", import.meta.url).pathname;
const MAX_FAST = 4;

const { frames, marks, end } = JSON.parse(readFileSync(`${OUT}timeline.json`, "utf8"));

const CUT = 1e6; // marks at or above this speed are gaps between Chrome sessions: dropped

// Segments [start, stop) with an effective speed.
const segments = marks.map((m, i) => ({
  start: m.t,
  stop: i + 1 < marks.length ? marks[i + 1].t : end,
  speed: m.speed,
}));

// A sped-up stretch may be split by gaps; cap the whole stretch, not each piece.
for (let i = 0; i < segments.length; ) {
  if (segments[i].speed <= 1 || segments[i].speed >= CUT) {
    i++;
    continue;
  }
  let j = i;
  let length = 0;
  while (j < segments.length && segments[j].speed > 1) {
    if (segments[j].speed < CUT) length += segments[j].stop - segments[j].start;
    j++;
  }
  for (let k = i; k < j; k++) {
    if (segments[k].speed < CUT) segments[k].speed = Math.max(segments[k].speed, length / MAX_FAST);
  }
  i = j;
}

/** Seconds on screen for the real interval [a, b). */
function outLength(a, b) {
  let total = 0;
  for (const s of segments) {
    const lo = Math.max(a, s.start);
    const hi = Math.min(b, s.stop);
    if (hi > lo) total += (hi - lo) / s.speed;
  }
  // Before the first mark (startup) plays at normal speed.
  if (segments.length && a < segments[0].start) total += Math.min(b, segments[0].start) - a;
  return total;
}

// Place each frame on the output timeline, then sample it at 25 fps: frames shorter than one
// output frame would otherwise each be stretched to the demuxer's minimum and add up.
const FPS = 25; // the image demuxer gives each picture at least 1/25 s
const starts = [];
let seconds = 0;
frames.forEach((f, i) => {
  starts.push(seconds);
  seconds += outLength(f.t, i + 1 < frames.length ? frames[i + 1].t : end);
});
const picks = [];
for (let k = 0, i = 0; k < Math.round(seconds * FPS); k++) {
  while (i + 1 < frames.length && starts[i + 1] <= k / FPS) i++;
  if (picks.length && picks.at(-1).i === i) picks.at(-1).n++;
  else picks.push({ i, n: 1 });
}
let list = "ffconcat version 1.0\n";
for (const p of picks) list += `file '${frames[p.i].file}'\nduration ${(p.n / FPS).toFixed(5)}\n`;
list += `file '${frames[picks.at(-1).i].file}'\nduration ${(1 / FPS).toFixed(5)}\n`; // ffconcat needs the last file twice
writeFileSync(`${OUT}frames.txt`, list);

execFileSync(
  "ffmpeg",
  [
    "-y", "-loglevel", "error",
    "-f", "concat", "-safe", "0", "-i", `${OUT}frames.txt`,
    "-t", seconds.toFixed(2), // otherwise ffmpeg holds the last picture for its full duration again
    "-vf", "fps=25,scale=1920:-2:flags=lanczos,format=yuv420p",
    "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-movflags", "+faststart",
    `${OUT}demo.mp4`,
  ],
  { stdio: "inherit" },
);
const real = end - frames[0].t;
console.log(`out/demo.mp4: ${Math.round(seconds)} s (recorded ${Math.round(real)} s)`);
