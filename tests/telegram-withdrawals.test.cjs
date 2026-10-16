const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../wallet.js'), 'utf8');
const address = '11111111111111111111111111111111';

async function fixture(options = {}) {
    const nodes = new Map();
    const timers = [];
    const makeNode = () => ({
        textContent: '', disabled: false, style: {}, children: [],
        addEventListener() {}, setAttribute() {},
        replaceChildren(...children) { this.children = children; },
        appendChild(child) { this.children.push(child); },
    });
    for (const id of ['withdraw-zrl-btn', 'withdraw-sol-btn', 'asset-zrl-bal', 'asset-sol-bal',
        'wallet-trusted-destination', 'pending-withdrawals']) {
        nodes.set(id, makeNode());
    }
    const calls = [], alerts = [];
    const answers = options.answers || ['1', address, '123456'];
    const state = { success: true, telegramId: '200', withdrawalMode: 'trusted_destination_v2',
        walletAddress: '', availableZrl: 25, gameSolBalance: 2, farms: [],
        pendingWithdrawalApprovals: [], ...options.state };
    const window = {
        Telegram: { WebApp: { initData: 'synthetic-not-a-real-session',
            showAlert(text) { assert(text.length <= 256); alerts.push(text); },
            showConfirm(text, done) { calls.push({ confirm: text }); done(options.confirm !== false); },
        } },
        crypto: { randomUUID: () => 'synthetic-request-123456' },
        showCustomPrompt(title, detail, placeholder, done) { calls.push({ prompt: title }); done(answers.shift() ?? null); },
    };
    const context = { window, document: {
        getElementById: id => nodes.get(id) || null,
        createElement: makeNode,
    },
        console: { warn() {} }, setTimeout(callback, delay) {
            timers.push({ callback, delay });
            return timers.length;
        }, clearTimeout() {},
        async fetch(url, settings) {
            const route = new URL(url).pathname, body = JSON.parse(settings.body);
            calls.push({ route, body });
            let result = { success: true };
            let ok = true;
            if (route === '/wallet/state') result = { ...state };
            if (route === '/wallet/withdrawals/request') {
                if (options.autoWithdraw) {
                    result = { success: true, status: 'submitted', txHash: 'synthetic-tx' };
                    state.pendingWithdrawals = [{ nonce: 'auto-nonce', asset: body.asset, status: 'submitted' }];
                } else {
                result = { success: true, nonce: 'server-nonce', address: body.address, amount: body.amount, asset: body.asset };
                    state.pendingWithdrawalApprovals = [result];
                }
            }
            if (route === '/wallet/withdrawals/confirm') {
                if (options.rejectCode) { ok = false; result = { error: 'Incorrect verification code.' }; }
                else state.pendingWithdrawalApprovals = [];
            }
            if (route === '/wallet/withdrawals/cancel') state.pendingWithdrawalApprovals = [];
            return { ok, status: ok ? 200 : 403, json: async () => result };
        },
    };
    vm.runInNewContext(source, context);
    await new Promise(resolve => setImmediate(resolve));
    calls.length = 0;
    return { api: window.secureWallet, state, calls, alerts, nodes, answers, timers, window };
}

test('both assets withdraw without creating a wallet, with explicit address and Telegram code', async () => {
    for (const asset of ['SOL', 'ZRL']) {
        const f = await fixture();
        await Promise.all([f.api.withdraw(asset), f.api.withdraw(asset)]);
        const requests = f.calls.filter(x => x.route === '/wallet/withdrawals/request');
        assert.equal(requests.length, 1);
        assert.equal(requests[0].body.asset, asset);
        assert.equal(requests[0].body.address, address);
        assert.equal(f.calls.filter(x => x.route === '/wallet/withdrawals/confirm').length, 1);
        assert.equal(f.calls.find(x => x.route === '/wallet/withdrawals/confirm').body.code, '123456');
        assert(!f.calls.some(x => /\/wallet\/(link|challenge)$/.test(x.route || '')));
        assert.equal(f.state.availableZrl, 25);
        assert.equal(f.state.gameSolBalance, 2);
    }
});

test('a saved destination sends subsequent withdrawals without another Telegram code', async () => {
    const f = await fixture({
        state: { trustedWithdrawalAddress: address },
        autoWithdraw: true,
    });
    await f.api.withdraw('ZRL');
    assert.equal(f.calls.filter(x => x.route === '/wallet/withdrawals/request').length, 1);
    assert.equal(f.calls.filter(x => x.route === '/wallet/withdrawals/confirm').length, 0);
    assert(f.alerts.some(text => text.includes('Withdrawal submitted to Solana')));
    assert(f.nodes.get('wallet-trusted-destination').textContent.includes('no repeat code'));
});

test('pending transfers show status only and refresh automatically without a manual button', async () => {
    const f = await fixture({
        state: {
            pendingWithdrawals: [{
                nonce: 'pending-zrl', asset: 'ZRL', amount: 2000, address,
                txHash: 'synthetic-signature', status: 'submitted',
            }],
        },
    });
    const pendingRow = f.nodes.get('pending-withdrawals').children[0];
    assert.equal(pendingRow.children.length, 1);
    assert(pendingRow.children[0].textContent.includes('ZRL 2000.00'));
    assert(pendingRow.children[0].textContent.includes('awaiting final Solana confirmation'));
    assert.equal(f.timers[0].delay, 15000);
    assert(!f.calls.some(x => x.route === '/wallet/withdrawals/status'));
});

test('cancelling amount, address, or destination confirmation never requests payment', async () => {
    for (const options of [{ answers: [] }, { answers: ['1'] }, { confirm: false }, { answers: ['1','bad'] }]) {
        const f = await fixture(options);
        await f.api.withdraw('SOL');
        assert(!f.calls.some(x => x.route === '/wallet/withdrawals/request'));
    }
});

test('cancelled OTP stays pending and can resume after reload without another request', async () => {
    const f = await fixture({ answers: ['1', address] });
    await f.api.withdraw('ZRL');
    assert.equal(f.state.pendingWithdrawalApprovals.length, 1);
    const reloaded = await fixture({ state: { pendingWithdrawalApprovals: f.state.pendingWithdrawalApprovals }, answers: ['123456'] });
    await reloaded.api.withdraw('ZRL');
    assert(!reloaded.calls.some(x => x.route === '/wallet/withdrawals/request'));
    assert.equal(reloaded.calls.filter(x => x.route === '/wallet/withdrawals/confirm').length, 1);
});

test('wrong code is shown and the server-owned request remains available', async () => {
    const f = await fixture({ rejectCode: true });
    await f.api.withdraw('SOL');
    assert(f.alerts.includes('Incorrect verification code.'));
    assert.equal(f.state.pendingWithdrawalApprovals.length, 1);
});

test('cancel request uses its nonce and never sends a payout', async () => {
    const f = await fixture({ state: { pendingWithdrawalApprovals: [{ nonce:'pending',asset:'SOL' }] } });
    await f.api.approveWithdrawal('pending', true);
    assert.equal(f.calls.filter(x => x.route === '/wallet/withdrawals/cancel').length, 1);
    assert(!f.calls.some(x => x.route === '/wallet/withdrawals/confirm'));
});

test('old server, missing identity, zero funds and pending chain payment fail closed', async () => {
    for (const state of [{ withdrawalMode:undefined }, {telegramId:undefined}, {gameSolBalance:0},
        {pendingWithdrawals:[{asset:'SOL',status:'processing'}]}]) {
        const f = await fixture({ state });
        await f.api.withdraw('SOL');
        assert(!f.calls.some(x => x.route === '/wallet/withdrawals/request'));
        assert(f.alerts.length);
    }
});

test('switching Telegram profile while entering an amount prevents a withdrawal request', async () => {
    const f = await fixture();
    const prompt = f.window.showCustomPrompt;
    f.window.showCustomPrompt = (...args) => {
        f.window.Telegram.WebApp.initData = 'different-synthetic-session';
        prompt(...args);
    };
    await f.api.withdraw('SOL');
    assert(!f.calls.some(x => x.route === '/wallet/withdrawals/request'));
    assert(f.alerts.some(text => text.includes('Your Telegram profile changed')));
});
