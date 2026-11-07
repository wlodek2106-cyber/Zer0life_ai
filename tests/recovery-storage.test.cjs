'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { prefix, profiles, initialStorage, browser } = require('./helpers/recovery-storage.cjs');

test('restart after every interrupted localStorage write preserves both profiles exactly', () => {
    // Backup, farms, ZRL and SOL are separate writes, not a storage transaction.
    for (let failAt = 1; failAt <= 4; failAt++) {
        let storage = initialStorage();
        const other = [...storage].filter(([key]) => key.startsWith(prefix(300)));
        const first = browser(storage, 200, failAt);
        assert.throws(() => first.api.preserve(prefix(200)), /Synthetic storage interruption/);
        storage = new Map(JSON.parse(JSON.stringify([...storage]))); // Fresh JS runtime.
        const retry = browser(storage, 200);
        const amounts = retry.api.preserve(prefix(200));
        assert.equal(amounts.zrl, 100.000001);
        assert(Math.abs(amounts.sol - .01) < 1e-12);
        assert.equal(storage.get(prefix(200) + 'legacy_pending_farms'), profiles[200].active_farms);
        const backup = JSON.parse(storage.get(prefix(200) + 'legacy_recovery_backup_v1'));
        for (const [key, value] of Object.entries(profiles[200])) assert.equal(backup[key], value);
        const frozen = [...storage];
        for (let n = 0; n < 3; n++) retry.api.preserve(prefix(200));
        assert.deepEqual([...storage], frozen);
        assert.deepEqual([...storage].filter(([key]) => key.startsWith(prefix(300))), other);
    }
});

test('new verified farm list being empty does not erase farms archived before dashboard writes', () => {
    const storage = initialStorage();
    const view = browser(storage, 200);
    view.api.preserve(prefix(200));
    storage.set(prefix(200) + 'active_farms', '[]');
    storage.set(prefix(200) + 'total_balance', '28.00001');
    view.api.preserve(prefix(200));
    view.load('legacy-farms.js');
    view.context.window.legacyFarmReview.setState({
        farms: [], restoredLegacyFarmIds: [], legacyFarmSettlements: [],
    });
    assert.equal(storage.get(prefix(200) + 'legacy_pending_farms'), profiles[200].active_farms);
    assert.match(view.elements.get('legacy-farming-deposits-list').text, /1,000 ZRL/);
    assert.match(view.elements.get('legacy-farming-deposits-list').text, /0\.002 SOL/);
    assert.equal(view.elements.get('legacy-farms-count').text, '2 · under review');
    assert.equal(view.api.preserve(prefix(200)).zrl, 100.000001);
});

test('real wallet refresh sends the archive, not new spendable sums, and keeps local evidence', async () => {
    const storage = initialStorage();
    const before = [...storage];
    const view = browser(storage, 200);
    // Wallet renders only real dashboard elements. Missing ones are optional.
    view.context.document.getElementById = () => null;
    view.context.setTimeout = () => 1;
    view.context.console = console;
    const calls = [];
    view.context.fetch = async (url, options) => {
        const route = new URL(url).pathname;
        calls.push({ route, body: JSON.parse(options.body) });
        assert(['/wallet/legacy', '/wallet/state'].includes(route), `Unexpected route: ${route}`);
        return { ok: true, json: async () => route === '/wallet/legacy'
            ? { success: true, pendingReview: true }
            : { success: true, telegramId: '200', availableZrl: 28.00001,
                gameSolBalance: .00050001, legacyPendingZrl: 100.000001,
                legacyPendingSol: .01, farms: [], pendingWithdrawals: [] } };
    };
    view.load('wallet.js');
    await new Promise(resolve => setImmediate(resolve));
    await view.context.window.secureWallet.refresh();
    assert.deepEqual(calls.map(call => call.route),
        ['/wallet/legacy', '/wallet/state', '/wallet/legacy', '/wallet/state']);
    for (const call of calls.filter(call => call.route === '/wallet/legacy')) {
        assert.equal(call.body.amount, 100.000001);
        assert(Math.abs(call.body.solAmount - .01) < 1e-12);
        assert.equal(call.body.initData, 'synthetic-200');
    }
    assert.equal(view.context.window.secureWallet.getState().availableZrl, 28.00001);
    for (const [key, value] of before) assert.equal(storage.get(key), value);
    assert.equal(storage.get(prefix(200) + 'legacy_pending_farms'), profiles[200].active_farms);
    assert.equal(storage.has(prefix(300) + 'legacy_pending_zrl'), false);
});
