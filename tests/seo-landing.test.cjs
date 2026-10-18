'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const project = path.join(__dirname, '..');
const index = fs.readFileSync(path.join(project, 'index.html'), 'utf8');

test('landing page exposes crawlable Zer0Life Run information and valid WebApplication data', () => {
    assert.match(index, /<title>Zer0Life Run 2\.0 \| Telegram Move-to-Earn Running Game<\/title>/);
    assert.match(index, /<section class="seo-section" aria-labelledby="seo-heading">/);
    assert.match(index, /Telegram Move-to-Earn running game/);
    assert.match(index, /No invite code is required to register/);
    assert.match(index, /no financial return is guaranteed/i);

    const jsonLd = index.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/);
    assert.ok(jsonLd, 'WebApplication structured data is present in static HTML');
    const data = JSON.parse(jsonLd[1]);
    assert.equal(data['@type'], 'WebApplication');
    assert.equal(data.url, 'https://wlodek2106-cyber.github.io/Zer0life_ai/');
});

test('the Get Started button opens Telegram from a browser and preserves referral attribution', () => {
    assert.match(index, /const tg = window\.Telegram\?\.WebApp;/);
    assert.match(index, /const telegramBotUrl = new URL\('https:\/\/t\.me\/Zer0lifelabs_ai_bot'\)/);
    assert.match(index, /telegramBotUrl\.searchParams\.set\('start', `ref_\$\{referralMatch\[1\]\.toUpperCase\(\)\}`\)/);
    assert.match(index, /if \(!tg\?\.initDataUnsafe\?\.user\?\.id\)/);
    assert.match(index, /dashboardUrl\.searchParams\.set\('ref', referralMatch\[1\]\.toUpperCase\(\)\)/);
});

test('the landing page can scroll to the visible search content without changing its full-screen hero', () => {
    assert.match(index, /body\s*\{[\s\S]*?overflow-y:\s*auto/);
    assert.match(index, /\.main-container\s*\{[\s\S]*?height:\s*100dvh/);
    assert.match(index, /class="zl-device"/);
});
