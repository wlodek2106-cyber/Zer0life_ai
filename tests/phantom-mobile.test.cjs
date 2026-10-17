const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../phantom-mobile.js'), 'utf8');
const key = 'synthetic_phantom_mobile_pending';
const url = 'https://phantom.com/ul/v1/connect?app_url=https%3A%2F%2Fexample.test%2F&dapp_encryption_public_key=public-fixture&redirect_link=https%3A%2F%2Fapi.example.test%2Fcallback%3Fstage%3Dconnect';

function fixture(options = {}) {
    const storage = new Map();
    const nodes = new Map();
    for (const id of ['wallet-withdrawal-status', 'phantom-mobile-handoff',
        'phantom-native-open', 'phantom-universal-open']) {
        nodes.set(id, { hidden: true, textContent: '', href: '',
            removeAttribute(name) { delete this[name]; } });
    }
    const calls = [], opened = [], alerts = [], timers = [];
    let refreshes = 0;
    const statusResult = options.status || { status: 'connecting' };
    const result = { sessionId: 'synthetic-session', expiresAt: Date.now() / 1000 + 300,
        url: options.url || url };
    const window = {
        Telegram: { WebApp: { openLink(link) { opened.push(link); }, showAlert(text) { alerts.push(text); } } },
        open(link) { opened.push(link); },
        addEventListener() {},
        secureWallet: {
            async request(route, body) {
                calls.push({ route, body });
                if (route.endsWith('/start')) {
                    if (options.startError) throw Error('Synthetic start failure');
                    return result;
                }
                if (route.endsWith('/mobile/status')) return statusResult;
                if (route === '/wallet/link') return { success: true };
                if (route === '/wallet/withdraw') return { status: 'processing', txHash: 'synthetic-hash' };
                throw Error('Unexpected route ' + route);
            },
            async refresh() { refreshes++; },
        },
    };
    const context = { window, URL, Date, console, STORAGE_PREFIX: 'synthetic_',
        localStorage: {
            getItem(name) { return storage.get(name) ?? null; },
            setItem(name, value) {
                if (options.writeError) throw Error('Synthetic storage failure');
                storage.set(name, value);
            },
            removeItem(name) { storage.delete(name); },
        },
        document: {
            getElementById(id) { return options.noPanel ? null : nodes.get(id); },
            addEventListener() {},
        },
        setTimeout(fn) { timers.push(fn); return timers.length; }, clearTimeout() {},
    };
    vm.runInNewContext(source, context);
    return { api: window.phantomMobile, storage, nodes, calls, opened, alerts, timers,
        result, statusResult, refreshes: () => refreshes };
}

test('prepares native user-tapped Phantom link without automatically opening a browser', async () => {
    const f = fixture();
    await f.api.start('link');
    assert.equal(f.opened.length, 0);
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0].route, '/wallet/mobile/start');
    assert.equal(f.nodes.get('phantom-mobile-handoff').hidden, false);
    const native = new URL(f.nodes.get('phantom-native-open').href);
    assert.equal(native.protocol, 'phantom:');
    assert.equal(native.host, 'v1');
    assert.equal(native.pathname, '/connect');
    assert.equal(native.search, new URL(url).search);
    assert.equal(f.nodes.get('phantom-universal-open').href, url);
    assert.equal(JSON.parse(f.storage.get(key)).id, 'synthetic-session');
});

test('double taps and repeat Connect reuse the same pending approval', async () => {
    const f = fixture();
    await Promise.all([f.api.start('link'), f.api.start('link')]);
    await f.api.start('link');
    assert.equal(f.calls.length, 1);
    assert.equal(f.opened.length, 0);
});

test('untrusted URLs and missing UI fail explicitly, never falling back to a browser', async () => {
    for (const options of [
        { url: 'https://example.test/ul/v1/connect' },
        { url: 'https://phantom.com/ul/browse/https://example.test' },
        { url: 'https://evil@phantom.com/ul/v1/connect' },
        { url: 'javascript:alert(1)' }, { noPanel: true },
    ]) {
        const f = fixture(options);
        await assert.rejects(f.api.start('link'));
        assert.equal(f.storage.has(key), false);
        assert.equal(f.opened.length, 0);
        assert.equal(f.calls.filter(c => c.route === '/wallet/link').length, 0);
    }
});

test('storage or server errors leave no active launch link and allow a retry', async () => {
    for (const options of [{ writeError: true }, { startError: true }]) {
        const f = fixture(options);
        await assert.rejects(f.api.start('link'));
        assert.equal(f.nodes.get('phantom-mobile-handoff').hidden, true);
        assert.equal(f.opened.length, 0);
        assert.equal(f.storage.has(key), false);
        await assert.rejects(f.api.start('link'));
        assert.equal(f.calls.length, 2);
    }
});

test('returning to Telegram finishes verified linking once and hides the handoff', async () => {
    const f = fixture({ status: { status: 'complete', purpose: 'link',
        nonce: 'synthetic-nonce', address: 'synthetic-address', signature: 'synthetic-signature' } });
    await f.api.start('link');
    await f.api.resume();
    await f.api.resume();
    assert.equal(f.calls.filter(c => c.route === '/wallet/link').length, 1);
    assert.equal(f.calls.at(-1).body.signature, 'synthetic-signature');
    assert.equal(f.storage.has(key), false);
    assert.equal(f.nodes.get('phantom-mobile-handoff').hidden, true);
    assert.equal(f.nodes.get('phantom-native-open').href, undefined);
    assert.equal(f.refreshes(), 1);
});

test('signing stage does not offer a second connect that could invalidate the approval', async () => {
    const f = fixture({ status: { status: 'signing' } });
    await f.api.start('link');
    await f.api.resume();
    await f.api.start('link');
    assert.equal(f.nodes.get('phantom-mobile-handoff').hidden, true);
    assert.equal(f.calls.filter(c => c.route.endsWith('/start')).length, 1);
    assert.match(f.nodes.get('wallet-withdrawal-status').textContent, /Finish signing in Phantom/);
});

test('expired and cancelled approvals hide the native link without submitting funds', async () => {
    for (const expired of [false, true]) {
        const f = fixture({ status: { status: 'failed', error: 'Synthetic cancellation' } });
        await f.api.start('link');
        if (expired) {
            const pending = JSON.parse(f.storage.get(key));
            pending.expiresAt = 1;
            f.storage.set(key, JSON.stringify(pending));
        }
        await f.api.resume();
        assert.equal(f.storage.has(key), false);
        assert.equal(f.nodes.get('phantom-mobile-handoff').hidden, true);
        assert.equal(f.calls.some(c => c.route === '/wallet/link' || c.route === '/wallet/withdraw'), false);
    }
});

test('SOL and ZRL approvals retain the existing signed withdrawal route, with no duplicate submit', async () => {
    for (const purpose of ['withdraw_sol', 'withdraw_zrl']) {
        const f = fixture({ status: { status: 'complete', purpose,
            nonce: 'synthetic-withdrawal', address: 'synthetic-address', signature: 'synthetic-signature' } });
        await f.api.start(purpose, 'synthetic-withdrawal');
        assert.equal(f.calls[0].body.nonce, 'synthetic-withdrawal');
        await f.api.resume();
        await f.api.resume();
        assert.equal(f.calls.filter(c => c.route === '/wallet/withdraw').length, 1);
        assert.equal(f.calls.at(-1).body.nonce, 'synthetic-withdrawal');
        assert.equal(f.opened.length, 0);
    }
});

test('dashboard removes personal wallet UI while retaining game balances, referrals and withdrawals', () => {
    const html = fs.readFileSync(path.join(__dirname, '../dashboard.html'), 'utf8');
    for (const id of ['recovery-balances', 'recovery-admin-panel', 'recovery-admin-load', 'recovery-admin-apply']) {
        assert(!html.includes('id="' + id + '"'));
    }
    for (const id of ['wallet-withdrawal-status', 'pending-withdrawals', 'affiliate-levels',
        'withdraw-sol-btn', 'withdraw-zrl-btn', 'deposit-btn']) {
        assert(html.includes('id="' + id + '"'), 'Missing retained wallet element: ' + id);
    }
    for (const id of ['embedded-wallet-root', 'setup-embedded-wallet-btn', 'wallet-copy-address-btn',
        'wallet-export-btn', 'asset-phantom-sol', 'asset-phantom-zrl']) {
        assert(!html.includes('id="' + id + '"'), 'Removed wallet UI returned: ' + id);
    }
    assert(!html.includes('id="connect-phantom-btn"'));
    assert(!html.includes('id="phantom-mobile-handoff"'));
    assert(!html.includes('src="./phantom-mobile.js?'));
    assert(!html.includes('src="./wallet-embedded/embedded-wallet.js'));
    assert(!html.includes('content: "ASSET OVERVIEW"'));
    assert(html.includes('wallet.js?v=english-withdrawal-v3'));
    assert(html.includes('depositReference: activeDepositReference'));
    assert(html.includes('reference=${encodeURIComponent(reference)}'));
    assert(html.includes('Use the Solana Pay QR with your unique reference.'));
    assert(!html.includes('A verified sender address is required'));
    assert(!html.includes('Transfers cannot be credited'));
    assert(html.includes('window.legacyRecovery.preserve(STORAGE_PREFIX)'));
    assert(!source.includes('WebApp.openLink('));
    assert(!source.includes('window.open('));
});
