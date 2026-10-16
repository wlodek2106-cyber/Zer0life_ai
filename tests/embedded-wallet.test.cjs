const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../wallet.js'), 'utf8');

async function fixture({ linked = false, cancel = false, amount = '1', missingId = false } = {}) {
    const { createWalletController } = await import('../wallet-embedded/src/controller.js');
    const nodes = new Map();
    for (const id of ['embedded-wallet-label', 'embedded-wallet-address', 'embedded-wallet-status',
        'setup-embedded-wallet-btn', 'embedded-wallet-tools', 'wallet-copy-address-btn', 'wallet-export-btn',
        'asset-zrl-bal', 'asset-sol-bal', 'withdraw-zrl-btn', 'withdraw-sol-btn']) {
        nodes.set(id, { textContent: '', disabled: false, hidden: false, addEventListener() {} });
    }
    const calls = [], alerts = [], events = {};
    const state = { success: true, telegramId: missingId ? undefined : '200', walletAddress: linked ? 'wallet-200' : '',
        availableZrl: 25, gameSolBalance: 2, farms: [] };
    const bridge = createWalletController();
    const sdk = { ready: true, authenticated: true, walletsReady: true,
        user: { telegram: { telegramUserId: '200' }, linkedAccounts: [{
            type: 'wallet', chainType: 'solana', walletClientType: 'privy', address: 'wallet-200',
        }] }, wallets: [{ address: 'wallet-200', standardWallet: { isPrivyWallet: true } }],
        async createWallet() { throw Error('Should reuse wallet'); },
        async signMessage(input) {
            calls.push({ sign: new TextDecoder().decode(input.message) });
            if (cancel) throw Error('User cancelled');
            return { signature: new Uint8Array(64).fill(8) };
        },
    };
    bridge.update(sdk);
    const window = {
        Telegram: { WebApp: { initData: 'synthetic-signed-telegram-data', showAlert(text) {
            assert(text.length <= 256, 'Telegram showAlert exceeds 256 UTF-16 units');
            alerts.push(text);
        } } },
        embeddedWallet: bridge,
        showCustomPrompt(title, detail, placeholder, resolve) { calls.push({ prompt: title }); resolve(amount); },
        addEventListener(name, fn) { events[name] = fn; },
        location: { reload() {} },
    };
    const context = {
        window, document: { getElementById: id => nodes.get(id) || null },
        console: { warn() {} }, setTimeout() {}, TextEncoder, Uint8Array,
        btoa: input => Buffer.from(input, 'binary').toString('base64'),
        async fetch(url, options) {
            const route = new URL(url).pathname, body = JSON.parse(options.body);
            calls.push({ route, body });
            let result = { success: true };
            if (route === '/wallet/state') result = { ...state };
            if (route === '/wallet/challenge') result = { success: true, nonce: 'server-nonce', message: 'exact server challenge' };
            if (route === '/wallet/link') state.walletAddress = 'wallet-200';
            if (route === '/wallet/withdraw') result = { success: true, status: 'processing', txHash: 'synthetic-hash' };
            return { ok: true, json: async () => result };
        },
    };
    vm.runInNewContext(source, context);
    await new Promise(resolve => setImmediate(resolve));
    calls.length = 0;
    return { api: window.secureWallet, state, nodes, calls, alerts, bridge, sdk, events };
}

test('activation uses server identity and signed challenge once, without a browser handoff or balance mutation', async () => {
    const f = await fixture();
    await Promise.all([f.api.connect(), f.api.connect()]);
    assert.equal(f.calls.filter(call => call.route === '/wallet/link').length, 1);
    assert.equal(f.calls.filter(call => call.sign).length, 1);
    assert.equal(f.calls.find(call => call.sign).sign, 'exact server challenge');
    assert.equal(f.state.availableZrl, 25);
    assert.equal(f.state.gameSolBalance, 2);
    assert.equal(f.nodes.get('setup-embedded-wallet-btn').hidden, true);
    assert.equal(f.nodes.get('withdraw-sol-btn').disabled, false);
    assert(f.calls.every(call => !call.route?.includes('/mobile/')));
});

test('both withdrawal assets sign the exact challenge and submit once despite repeated clicks', async () => {
    for (const asset of ['SOL', 'ZRL']) {
        const f = await fixture({ linked: true });
        await Promise.all([f.api.withdraw(asset), f.api.withdraw(asset)]);
        assert.equal(f.calls.filter(call => call.prompt).length, 1);
        assert.equal(f.calls.filter(call => call.route === '/wallet/withdraw').length, 1);
        const challenge = f.calls.find(call => call.route === '/wallet/challenge');
        assert.equal(challenge.body.purpose, asset === 'SOL' ? 'withdraw_sol' : 'withdraw_zrl');
        assert.equal(challenge.body.address, 'wallet-200');
        assert.equal(f.calls.find(call => call.sign).sign, 'exact server challenge');
    }
});

test('cancelling amount or signature never submits a withdrawal or changes funds', async () => {
    for (const options of [{ amount: null }, { cancel: true }]) {
        const f = await fixture({ linked: true, ...options });
        await f.api.withdraw('ZRL');
        assert.equal(f.calls.filter(call => call.route === '/wallet/withdraw').length, 0);
        assert.equal(f.state.availableZrl, 25);
    }
});

test('missing updated server identity blocks activation and withdrawal without signing', async () => {
    const f = await fixture({ linked: true, missingId: true });
    await f.api.withdraw('SOL');
    assert(f.nodes.get('withdraw-sol-btn').disabled);
    assert(!f.calls.some(call => call.sign || call.route === '/wallet/challenge'));
    assert(f.alerts.length);
});

test('another Telegram session cannot sign or withdraw from the displayed game account', async () => {
    const f = await fixture({ linked: true });
    f.bridge.update({ ...f.sdk, user: { telegram: { telegramUserId: '201' }, linkedAccounts: [] } });
    f.events['zerolife:embedded-wallet']();
    assert(f.nodes.get('withdraw-zrl-btn').disabled);
    await f.api.withdraw('ZRL');
    assert(!f.calls.some(call => call.sign || call.route === '/wallet/withdraw'));
    assert.match(f.alerts.at(-1), /не совпадает/);
});

test('invalid amounts and unsupported assets never create a signing challenge', async () => {
    for (const amount of ['0', '-1', 'NaN', '100']) {
        const f = await fixture({ linked: true, amount });
        await f.api.withdraw('SOL');
        assert(!f.calls.some(call => call.route === '/wallet/challenge'));
    }
    const f = await fixture({ linked: true });
    await f.api.withdraw('BTC');
    assert(!f.calls.some(call => call.route === '/wallet/challenge'));
});

test('long SDK errors respect the real Telegram alert limit and do not leave withdrawal locked', async () => {
    const f = await fixture({ linked: true });
    f.sdk.signMessage = async () => { throw Error('Ошибка SDK: ' + '🔒'.repeat(300)); };
    f.bridge.update({ ...f.sdk });
    await f.api.withdraw('SOL');
    assert(f.alerts.at(-1).length <= 256);
    assert(!f.calls.some(call => call.route === '/wallet/withdraw'));
    assert.equal(f.nodes.get('withdraw-sol-btn').disabled, false);
});
