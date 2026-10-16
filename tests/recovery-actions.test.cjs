const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../recovery-actions.js'), 'utf8');

function fixture(options = {}) {
    const nodes = new Map();
    const element = () => ({
        disabled: false, textContent: '', style: {}, children: [], handlers: {},
        append(...children) { this.children.push(...children); },
        appendChild(child) { this.children.push(child); },
        replaceChildren(...children) { this.children = children; },
        addEventListener(name, handler) { this.handlers[name] = handler; },
    });
    for (const id of ['recovery-action-status', 'recovery-admin-load', 'recovery-admin-apply',
        'recovery-admin-list', 'recovery-admin-panel']) nodes.set(id, element());
    const calls = [], messages = [], confirmations = [];
    let refreshCount = 0;
    const records = [{ requestId: 'review-a', digest: 'frozen-a', username: 'Synthetic',
        telegramId: '200', totalZrl: 123456.789123, totalSol: 0,
        farmCount: 16, farmZrl: 120000, farmSol: 0, farmYieldZrl: 3456.789123,
        yieldPendingCount: 0 }];
    const window = {
        Telegram: { WebApp: {
            showConfirm(message, callback) {
                messages.push(message);
                // Same constraint as the real Telegram SDK, not an unrestricted fake.
                if (message.length > 256) throw Error('WebAppPopupParamInvalid');
                if (options.popupError) throw Error(options.popupError);
                if (options.delay) confirmations.push(callback);
                else callback(options.approve !== false);
            },
        }},
        secureWallet: {
            async request(route, body) {
                calls.push({ route, body });
                if (route.endsWith('/list')) return { requests: records };
                if (options.apiError) throw Error(options.apiError);
                return { receipts: [{ requestId: 'review-a' }] };
            },
            async refresh() { refreshCount++; if (options.refreshError) throw Error('Refresh failed'); },
        },
    };
    vm.runInNewContext(source, { window, document: {
        getElementById: id => nodes.get(id), createElement: element,
    }});
    return { nodes, calls, messages, confirmations, window, records,
        click: id => nodes.get(id).handlers.click(), refreshCount: () => refreshCount };
}

test('real Telegram popup constraint accepts exact amounts and consent before any credit', async () => {
    const f = fixture();
    await f.click('recovery-admin-load');
    await f.click('recovery-admin-apply');
    assert.equal(f.messages.length, 1);
    assert(f.messages[0].length <= 256);
    assert.match(f.messages[0], /123456\.789123 ZRL и 0\.000000000 SOL/);
    assert.match(f.messages[0], /реальные средства проекта/);
    assert.equal(f.calls.filter(c => c.route.endsWith('/apply')).length, 1);
    assert.equal(f.calls.at(-1).body.requests[0].digest, 'frozen-a');
    assert.equal(f.refreshCount(), 1);
    assert.equal(f.nodes.get('recovery-admin-apply').disabled, true);
    assert.match(f.nodes.get('recovery-action-status').textContent, /Перенос выполнен/);
});

test('cancel does not call the financial endpoint and allows another explicit attempt', async () => {
    const f = fixture({ approve: false });
    await f.click('recovery-admin-load');
    await f.click('recovery-admin-apply');
    assert.equal(f.calls.length, 1);
    assert.equal(f.nodes.get('recovery-admin-apply').disabled, false);
    assert.equal(f.nodes.get('recovery-admin-load').disabled, false);
    assert.match(f.nodes.get('recovery-action-status').textContent, /Балансы не изменены/);
});

test('SDK rejection is visible, never credits, and never leaves controls stuck', async () => {
    for (const popupError of ['WebAppPopupOpened', 'WebAppMethodUnsupported', 'WebAppPopupParamInvalid']) {
        const f = fixture({ popupError });
        await f.click('recovery-admin-load');
        await f.click('recovery-admin-apply');
        assert.equal(f.calls.length, 1);
        assert.equal(f.nodes.get('recovery-admin-apply').disabled, false);
        assert.equal(f.nodes.get('recovery-admin-load').disabled, false);
        assert(f.nodes.get('recovery-action-status').textContent.length > 20);
    }
});

test('double tapping while native confirmation is open cannot queue another popup or credit', async () => {
    const f = fixture({ delay: true });
    await f.click('recovery-admin-load');
    const first = f.click('recovery-admin-apply');
    await f.click('recovery-admin-apply');
    await f.click('recovery-admin-load');
    assert.equal(f.messages.length, 1);
    assert.equal(f.calls.length, 1);
    f.confirmations[0](true);
    await first;
    assert.equal(f.calls.filter(c => c.route.endsWith('/apply')).length, 1);
});

test('an API error is visible and a failed refresh never claims the credit did not occur', async () => {
    const failed = fixture({ apiError: 'Snapshot expired' });
    await failed.click('recovery-admin-load');
    await failed.click('recovery-admin-apply');
    assert.equal(failed.nodes.get('recovery-admin-apply').disabled, false);
    assert.match(failed.nodes.get('recovery-action-status').textContent, /Snapshot expired/);
    const credited = fixture({ refreshError: true });
    await credited.click('recovery-admin-load');
    await credited.click('recovery-admin-apply');
    assert.match(credited.nodes.get('recovery-action-status').textContent, /Перенос выполнен, но/);
    assert.equal(credited.nodes.get('recovery-admin-apply').disabled, true);
});
