const assert = require("node:assert/strict");
const { existsSync, readFileSync } = require("node:fs");
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
  const cta = html.match(/<button class="zl-cta"[\s\S]*?<\/button>/)?.[0] ?? "";
  assert.match(cta, /<span>Get Started<\/span>/);
  assert.doesNotMatch(cta, /zl-arrow|↗/);
  assert.match(html, /id="start-btn"/);
  assert.match(html, /tg\.HapticFeedback\.impactOccurred\('medium'\)/);
  assert.match(html, /new URL\('dashboard\.html', window\.location\.href\)/);
  assert.match(html, /searchParams\.set\('build', 'boots-solana-pay-boots-address-v1'\)/);
  assert.match(html, /searchParams\.set\('ref', referralMatch\[1\]\.toUpperCase\(\)\)/);
});

test("welcome uses realistic runner artwork without a cartoon mascot", () => {
  assert.match(html, /<img class="zl-art" src="\.\/assets\/welcome-runner-3d-4k\.jpg"/);
  assert.match(html, /alt="A runner on a futuristic illuminated track"/);
  assert.match(html, /object-fit:\s*cover/);
  assert.equal(existsSync(join(__dirname, "..", "assets", "welcome-runner-3d-4k.jpg")), true);
  assert.doesNotMatch(html, /zerolife-run-race-mascot|racing cat|<svg class="zl-art"/i);
});

test("welcome supports reduced motion and visible keyboard focus", () => {
  assert.match(html, /prefers-reduced-motion:\s*reduce/);
  assert.match(html, /:focus-visible/);
  assert.match(html, /env\(safe-area-inset-(top|bottom)\)/);
});
