const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../wallet.js'), 'utf8');

async function fixture({ outcome = 'confirmed', dropResponse = false, cached = false, enabled = true } = {}) {
    const storage = new Map(), calls = [], labels = new Map();
    for (const id of ['booster-payment-status', 'booster-payment-tx']) labels.set(id, { textContent: '', hidden: true });
    const localStorage = {
        getItem: key => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, value),
        removeItem: key => storage.delete(key),
    };
    if (cached) localStorage.setItem('zerolife-merchant-purchase:200', JSON.stringify({ eventId: 'saved-id', costSol: .2 }));
    const account = { success: true, referralCode: 'TESTUSER', availableZrl: 25,
        gameSolBalance: 2, farms: [], boosterRate: 300, merchantPaymentsEnabled: enabled, pendingPurchases: [] };
    const window = { Telegram: { WebApp: { initData: 'synthetic', initDataUnsafe: { user: { id: 200 } } } },
        addEventListener() {}, crypto: { randomUUID: () => 'new-purchase-id' } };
    const context = {
        window, localStorage, document: { getElementById: id => labels.get(id) || null },
        console: { warn() {} }, setTimeout: (fn, delay) => { if (delay === 3000) setImmediate(fn); },
        async fetch(url, options) {
            const route = new URL(url).pathname, body = JSON.parse(options.body);
            calls.push({ route, body });
            let result = { success: true };
            if (route === '/wallet/state') result = { ...account };
            if (route === '/wallet/spend') {
                account.gameSolBalance = 1.8;
                if (dropResponse) throw new TypeError('Synthetic response lost');
                result = { success: true, purchaseId: body.eventId, status: 'pending', gameSolBalance: 1.8 };
            }
            if (route === '/wallet/purchases/status') {
                account.boosterRate = outcome === 'confirmed' ? 1500 : 300;
                account.gameSolBalance = outcome === 'failed' ? 2 : 1.8;
                result = { ...account, purchaseId: body.purchaseId, status: outcome,
                    txHash: 'synthetic-signature', message: 'Synthetic payment failed; SOL restored' };
            }
            return { ok: true, json: async () => result };
        },
    };
    vm.runInNewContext(source, context);
    await new Promise(resolve => setImmediate(resolve));
    calls.length = 0;
    return { api: window.secureWallet, storage, calls, labels, account };
}

test('Boots waits for finality and persists its id before a single spend request', async () => {
    const f = await fixture();
    const result = await f.api.spend('booster', { costSol: .2, eventId: 'attacker-id' });
    assert.equal(result.status, 'confirmed');
    assert.equal(f.api.getState().boosterRate, 1500);
    assert.equal(f.calls.filter(c => c.route === '/wallet/spend').length, 1);
    assert.equal(f.calls.find(c => c.route === '/wallet/spend').body.eventId, 'new-purchase-id');
    assert.equal(f.storage.size, 0);
    assert.match(f.labels.get('booster-payment-status').textContent, /confirmed/);
    assert.equal(f.labels.get('booster-payment-status').hidden, false);
    assert.match(f.labels.get('booster-payment-tx').href, /^https:\/\/explorer.solana.com\/tx\//);
});

test('double click cannot create another payment while the first is running', async () => {
    const f = await fixture();
    const first = f.api.spend('booster', { costSol: .2 });
    await assert.rejects(f.api.spend('booster', { costSol: .2 }), /Another charge is blocked/);
    await first;
    assert.equal(f.calls.filter(c => c.route === '/wallet/spend').length, 1);
});

test('lost spend response keeps the payment id so a retry cannot debit a second time', async () => {
    const f = await fixture({ dropResponse: true });
    await assert.rejects(f.api.spend('booster', { costSol: .2 }), /response lost/);
    const saved = JSON.parse([...f.storage.values()][0]);
    assert.equal(saved.eventId, 'new-purchase-id');
    assert.equal(f.labels.get('booster-payment-status').textContent, '');
    assert.equal(f.labels.get('booster-payment-status').hidden, true);
    await assert.rejects(f.api.spend('booster', { costSol: .2 }));
    assert.deepEqual(f.calls.filter(c => c.route === '/wallet/spend').map(c => c.body.eventId),
        ['new-purchase-id', 'new-purchase-id']);
});

test('failed merchant transfer restores displayed SOL and does not activate Boots', async () => {
    const f = await fixture({ outcome: 'failed' });
    await assert.rejects(f.api.spend('booster', { costSol: .2 }), /SOL restored/);
    assert.equal(f.api.getState().gameSolBalance, 2);
    assert.equal(f.api.getState().boosterRate, 300);
    assert.equal(f.storage.size, 0);
    assert.match(f.labels.get('booster-payment-status').textContent, /SOL restored/);
});

test('old backend cannot be charged by the updated purchase UI', async () => {
    const f = await fixture({ enabled: false });
    await assert.rejects(f.api.spend('booster', { costSol: .2 }), /has not been updated yet/);
    assert.equal(f.calls.length, 0);
    assert.equal(f.storage.size, 0);
});

test('an existing payment is resumed on refresh without a new spend request', async () => {
    const f = await fixture({ cached: true });
    assert.equal(f.calls.filter(c => c.route === '/wallet/spend').length, 0);
    assert.equal(f.storage.size, 0);
    assert.equal(f.api.getState().boosterRate, 1500);
});

test('pending purchase never reports activation and keeps its id for the next session', async () => {
    const f = await fixture({ outcome: 'pending' });
    const result = await f.api.spend('booster', { costSol: .2 });
    assert.equal(result.status, 'pending');
    assert.equal(f.api.getState().boosterRate, 300);
    assert.equal(f.storage.size, 1);
    assert.match(f.labels.get('booster-payment-status').textContent, /still being checked/);
});

test('game store requires confirmed status before showing success or closing', () => {
    const html = fs.readFileSync(path.join(__dirname, '../dashboard.html'), 'utf8');
    const purchase = html.slice(html.indexOf('let boosterPurchaseBusy'), html.indexOf('window.openDaoModal'));
    assert(purchase.indexOf("result.status !== 'confirmed'") < purchase.indexOf("boosters-modal').style.display = 'none'"));
    assert.match(purchase, /finally\s*\{\s*boosterPurchaseBusy = false/);
    assert.match(html, /<p id="booster-payment-status" role="status" aria-live="polite" hidden/);
    assert.match(html, /VIP Boosters Store<\/span>/);
    assert.doesNotMatch(html, /Merchant Flow|Boots payment\s*→\s*57S7TryAMhxRq5sMSyZxTkyCmzT8tPFKfXjzZwvmv5db/);
    assert.doesNotMatch(source, /Check the payment status in Wallet\. Do not send another payment manually\./);
});
