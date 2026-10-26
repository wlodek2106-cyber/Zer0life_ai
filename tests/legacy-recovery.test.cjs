const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../legacy-recovery.js'), 'utf8');
const prefix = 'zrl_user_fixture_';

function setup(values = {}, failWrites = false) {
    const storage = new Map(Object.entries(values));
    const elements = new Map();
    const context = {
        STORAGE_PREFIX: prefix,
        window: { addEventListener() {} },
        localStorage: {
            getItem(key) { return storage.has(key) ? storage.get(key) : null; },
            setItem(key, value) {
                if (failWrites) throw Error('Storage unavailable');
                storage.set(key, String(value));
            },
        },
        document: {
            getElementById(id) {
                if (!elements.has(id)) elements.set(id, { textContent: '' });
                return elements.get(id);
            },
        },
    };
    vm.runInNewContext(source, context);
    return { api: context.window.legacyRecovery, storage, context,
        text: id => elements.get(id)?.textContent || '' };
}
function entries(values) {
    return Object.fromEntries(Object.entries(values).map(([key, value]) => [prefix + key, value]));
}

test('preserves raw history and farms before verified values can replace device fields', () => {
    const farms = '[{"id":"historical","amount":25,"asset":"zrl"}]';
    const view = setup(entries({ total_balance: '100', earned_zrl: '90', unclaimed: '5',
        pool_deposit: '0.02', casino_total_won: '0.01', casino_turnover: '0.005', active_farms: farms }));
    const result = view.api.preserve(prefix);
    assert.equal(result.zrl, 105);
    assert(Math.abs(result.sol - .025) < 1e-10);
    view.storage.set(prefix + 'active_farms', '[]');
    view.storage.set(prefix + 'total_balance', '7');
    assert.equal(view.api.preserve(prefix).zrl, 105);
    assert.equal(view.storage.get(prefix + 'legacy_pending_farms'), farms);
    const original = JSON.parse(view.storage.get(prefix + 'legacy_recovery_backup_v1'));
    assert.equal(original.total_balance, '100');
    assert.equal(original.active_farms, farms);
});
test('never adds the same snapshot again on refresh and never overwrites existing archives', () => {
    const view = setup(entries({ legacy_pending_zrl: '120', legacy_pending_sol: '.03',
        legacy_pending_farms: '{invalid-but-preserved', total_balance: '99999' }));
    for (let n = 0; n < 5; n++) assert.equal(view.api.preserve(prefix).zrl, 120);
    assert.equal(view.storage.get(prefix + 'legacy_pending_farms'), '{invalid-but-preserved');
    assert.equal(view.storage.get(prefix + 'total_balance'), '99999');
});
test('storage capture remains scoped to the selected profile', () => {
    const view = setup(entries({ total_balance: '900' }));
    view.api.preserve(prefix);
    assert.equal(view.api.preserve('zrl_user_other_').zrl, 0);
    assert.equal(view.storage.get(prefix + 'legacy_pending_zrl'), '900');
});
test('malformed financial values are preserved as evidence, not converted into available funds', () => {
    for (const amount of ['Infinity', 'NaN', 'broken', '-100']) {
        const view = setup(entries({ total_balance: amount }));
        assert.throws(() => view.api.preserve(prefix), /Invalid historical amount/);
        assert.equal(JSON.parse(view.storage.get(prefix + 'legacy_recovery_backup_v1')).total_balance, amount);
        assert.equal(view.storage.has(prefix + 'legacy_pending_zrl'), false);
    }
});
test('shows combined totals with a separate spendable amount without changing server data', () => {
    const view = setup();
    const state = Object.freeze({ availableZrl: 25, legacyPendingZrl: 5000, gameSolBalance: .005, legacyPendingSol: .015 });
    view.api.setState(state);
    view.api.setState(state);
    assert.equal(view.text('recovery-total-zrl'), '5025.00 ZRL');
    assert.equal(view.text('recovery-total-sol'), '0.020000000 SOL');
    assert.match(view.text('recovery-split-zrl'), /^25.00 available · 5000.00/);
    assert.match(view.text('recovery-status'), /Archived funds are not yet available to spend or withdraw/);
    assert.equal(state.availableZrl, 25);
});
test('does not pretend that an unloaded server balance is zero or restored', () => {
    const view = setup(entries({ legacy_pending_zrl: '5000', legacy_pending_sol: '.015' }));
    view.api.render();
    assert.equal(view.text('recovery-total-zrl'), 'Waiting for server');
    assert.match(view.text('header-legacy-balance'), /5000.00 ZRL/);
    assert.match(view.text('recovery-status'), /no total is available/);
});
test('a different device snapshot is not added again on top of the authoritative server archive', () => {
    const view = setup(entries({ legacy_pending_zrl: '700', legacy_pending_sol: '0' }));
    view.api.setState({ availableZrl: 5, legacyPendingZrl: 600, gameSolBalance: 0, legacyPendingSol: 0 });
    assert.equal(view.text('recovery-total-zrl'), '605.00 ZRL');
    assert.match(view.text('recovery-status'), /differs from the server copy/);
});
test('write failure is explicit and does not alter the original data', () => {
    const original = entries({ total_balance: '99', active_farms: '[1]' });
    const view = setup(original, true);
    assert.throws(() => view.api.preserve(prefix), /Storage unavailable/);
    assert.deepEqual(Object.fromEntries(view.storage), original);
});
test('history upload failure does not block the current authenticated wallet state', async () => {
    const calls = [], warnings = [];
    const window = { Telegram: { WebApp: { initData: 'synthetic-legacy-test-session' } },
        addEventListener() {}, legacyRecovery: {
        preserve: () => ({ zrl: 100, sol: 0 }),
        setWarning: message => warnings.push(message),
        setState() {},
    } };
    const context = {
        window, STORAGE_PREFIX: prefix, console, setTimeout() {},
        document: { getElementById: () => null },
        fetch: async url => {
            const route = new URL(url).pathname;
            calls.push(route);
            return { ok: route !== '/wallet/legacy', status: 400,
                json: async () => route === '/wallet/legacy'
                    ? { success: false, error: 'Simulated archival failure' }
                    : { success: true, availableZrl: 17 } };
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../wallet.js'), 'utf8'), context);
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(calls, ['/wallet/legacy', '/wallet/state']);
    assert.equal(window.secureWallet.getState().availableZrl, 17);
    assert(warnings.some(message => /has not synced with the server yet/.test(message)));
});
test('archive preservation remains functional with all recovery UI removed', () => {
    const farms = '[{"id":"synthetic-hidden","asset":"zrl","amount":100}]';
    const view = setup(entries({ total_balance: '150', active_farms: farms }));
    view.context.document.getElementById = () => null;
    assert.doesNotThrow(() => view.api.preserve(prefix));
    assert.doesNotThrow(() => view.api.setState({ availableZrl: 150, gameSolBalance: 0,
        legacyPendingZrl: 0, legacyPendingSol: 0, legacyBalanceRestored: true }));
    assert.equal(view.storage.get(prefix + 'total_balance'), '150');
    assert.equal(view.storage.get(prefix + 'active_farms'), farms);
    assert.equal(view.storage.get(prefix + 'legacy_pending_farms'), farms);
});

test('preservation is wired before dashboard mutations and never calls a payout API', () => {
    const html = fs.readFileSync(path.join(__dirname, '../dashboard.html'), 'utf8');
    assert(html.indexOf('src="./legacy-recovery.js?') < html.indexOf('const STORAGE_PREFIX'));
    assert(html.indexOf('window.legacyRecovery.preserve(STORAGE_PREFIX)') < html.indexOf('let totalBalance'));
    assert(!/\bfetch\s*\(|\/wallet\/withdraw|secureWallet/.test(source));
});
