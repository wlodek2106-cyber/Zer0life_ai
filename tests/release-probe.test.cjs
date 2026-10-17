const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../wallet.js'), 'utf8');

test('public release transport is a bounded uncached GET without a Telegram session', async () => {
    const calls = [], cleared = [];
    let fail = false, timeout;
    const window = {};
    vm.runInNewContext(source, {
        window, document: { getElementById() { return null; } },
        console: { warn() {} }, AbortController,
        setTimeout(fn, ms) { timeout = { fn, ms }; return 1; },
        clearTimeout(id) { cleared.push(id); },
        async fetch(url, settings) {
            calls.push({ url, settings });
            if (settings.method !== 'GET') throw Error('No Telegram session in this fixture');
            if (fail) throw Error('offline');
            return { ok: true, async json() { return { releaseId: 'public-fixture' }; } };
        },
    });
    const result = await window.secureWallet.getRelease();
    assert.equal(result.releaseId, 'public-fixture');
    const { url, settings } = calls.find(call => call.settings.method === 'GET');
    assert.equal(new URL(url).pathname, '/release');
    assert.equal(settings.method, 'GET');
    assert.equal(settings.cache, 'no-store');
    assert.equal(settings.credentials, 'omit');
    assert.equal(settings.body, undefined);
    assert.equal(settings.headers, undefined);
    assert.equal(timeout.ms, 8000);
    timeout.fn();
    assert.equal(settings.signal.aborted, true);
    fail = true;
    await assert.rejects(window.secureWallet.getRelease(), /offline/);
    assert.equal(cleared.length, 2);
});
