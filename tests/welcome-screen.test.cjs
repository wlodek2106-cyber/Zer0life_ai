const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");

const html = readFileSync(join(__dirname, "..", "index.html"), "utf8");

test("welcome screen is English and branded for Zer0Life Run 2.0", () => {
  assert.match(html, /<html lang="en">/);
  assert.match(html, /Zer0Life Run 2\.0/);
  assert.match(html, /Move to Earn/);
  assert.doesNotMatch(html, /[\u0400-\u04ff]/);
});

test("Get Started keeps the Telegram launch and referral handoff", () => {
  assert.match(html, /id="start-btn"/);
  assert.match(html, /tg\.HapticFeedback\.impactOccurred\('medium'\)/);
  assert.match(html, /new URL\('dashboard\.html', window\.location\.href\)/);
  assert.match(html, /searchParams\.set\('build', 'affiliate-v2'\)/);
  assert.match(html, /searchParams\.set\('ref', referralMatch\[1\]\.toUpperCase\(\)\)/);
});

test("welcome uses original 3D track art without a mascot", () => {
  assert.match(html, /<svg class="zl-art" viewBox="0 0 440 590"/);
  assert.match(html, /Sculpted three-dimensional running track in electric lime/);
  assert.match(html, /id="zl-track-top"/);
  assert.match(html, /id="zl-runner"/);
  assert.doesNotMatch(html, /zerolife-run-race-mascot|racing cat|<img\b/i);
});

test("welcome supports reduced motion and visible keyboard focus", () => {
  assert.match(html, /prefers-reduced-motion:\s*reduce/);
  assert.match(html, /:focus-visible/);
  assert.match(html, /env\(safe-area-inset-(top|bottom)\)/);
});
