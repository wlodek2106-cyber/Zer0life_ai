const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../dashboard.html'), 'utf8');
const statusHelper = html.match(/            const setDepositStatus = \(message\) => \{[\s\S]*?\n            \};/);
const addressHandler = html.match(/            const addressButton = document\.getElementById\('generate-deposit-addr-btn'\);\n            addressButton\.onclick = async \(\) => \{[\s\S]*?\n            \};/);

assert(statusHelper, 'Deposit status helper must exist');
assert(addressHandler, 'Deposit address handler must exist');

function createHarness(refresh, includeWallet = true) {
    const nodes = new Map([
        ['deposit-status-msg', { textContent: '', hidden: true }],
        ['deposit-details-box', { style: { display: 'none' } }],
        ['generate-deposit-addr-btn', { disabled: false, textContent: 'Show Deposit Address 🚀' }],
        ['deposit-source-warning', { textContent: '' }],
        ['deposit-full-addr', { innerText: '' }],
        ['deposit-qr-img', { src: '' }],
        ['check-deposit-status-btn', { disabled: false }],
        ['copy-addr-btn', { disabled: false }],
        ['tx-signature-input', { disabled: false }],
    ]);
    const context = {
        document: { getElementById: id => nodes.get(id) || null },
        window: includeWallet ? { secureWallet: { refresh } } : {},
        SHARED_POOL_WALLET: 'synthetic-pool-address',
        triggerHaptic() {},
        playSound() {},
        encodeURIComponent,
    };
    vm.runInNewContext(`${statusHelper[0]}\n${addressHandler[0]}`, context);
    return { nodes, click: () => nodes.get('generate-deposit-addr-btn').onclick() };
}

test('deposit status is visible outside the hidden deposit details area', () => {
    const statusStart = html.indexOf('<div id="deposit-status-msg"');
    const detailsStart = html.indexOf('<div id="deposit-details-box"');
    assert(statusStart >= 0 && statusStart < detailsStart);
    assert.match(html, /id="deposit-status-msg" role="status" aria-live="polite" hidden/);
});

test('a missing verified sender address still shows the pool address but blocks uncreditable transfers', async () => {
    const harness = createHarness(async () => ({ walletAddress: '' }));
    await harness.click();

    const status = harness.nodes.get('deposit-status-msg');
    assert.equal(status.hidden, false);
    assert.match(status.textContent, /verified sender address is required/i);
    assert.match(status.textContent, /No funds were moved/i);
    assert.equal(harness.nodes.get('deposit-details-box').style.display, 'block');
    assert.equal(harness.nodes.get('deposit-full-addr').innerText, 'synthetic-pool-address');
    assert.match(harness.nodes.get('deposit-source-warning').textContent, /Transfers cannot be credited/);
    assert.equal(harness.nodes.get('check-deposit-status-btn').disabled, true);
    assert.equal(harness.nodes.get('copy-addr-btn').disabled, true);
    assert.equal(harness.nodes.get('tx-signature-input').disabled, true);
    assert.equal(harness.nodes.get('generate-deposit-addr-btn').disabled, false);
});

test('wallet refresh failures are shown, then the deposit button becomes usable again', async () => {
    const harness = createHarness(async () => { throw new Error('Wallet API unavailable'); });
    await harness.click();

    assert.equal(harness.nodes.get('deposit-status-msg').hidden, false);
    assert.match(harness.nodes.get('deposit-status-msg').textContent, /Wallet API unavailable/);
    assert.equal(harness.nodes.get('generate-deposit-addr-btn').disabled, false);
    assert.equal(harness.nodes.get('deposit-details-box').style.display, 'block');
    assert.equal(harness.nodes.get('deposit-full-addr').innerText, 'synthetic-pool-address');
    assert.match(harness.nodes.get('deposit-source-warning').textContent, /Do not send SOL/);
    assert.equal(harness.nodes.get('check-deposit-status-btn').disabled, true);
});

test('a verified sender address reveals the shared pool address and QR code', async () => {
    const harness = createHarness(async () => ({ walletAddress: 'synthetic-verified-sender' }));
    await harness.click();

    assert.equal(harness.nodes.get('deposit-status-msg').hidden, true);
    assert.equal(harness.nodes.get('deposit-details-box').style.display, 'block');
    assert.match(harness.nodes.get('deposit-source-warning').textContent, /synthetic-verified-sender/);
    assert.equal(harness.nodes.get('deposit-full-addr').innerText, 'synthetic-pool-address');
    assert.match(harness.nodes.get('deposit-qr-img').src, /synthetic-pool-address/);
    assert.equal(harness.nodes.get('check-deposit-status-btn').disabled, false);
    assert.equal(harness.nodes.get('copy-addr-btn').disabled, false);
    assert.equal(harness.nodes.get('tx-signature-input').disabled, false);
    assert.equal(harness.nodes.get('generate-deposit-addr-btn').disabled, false);
});

test('missing wallet service shows a message instead of silently failing', async () => {
    const harness = createHarness(async () => null, false);
    await harness.click();

    assert.equal(harness.nodes.get('deposit-status-msg').hidden, false);
    assert.match(harness.nodes.get('deposit-status-msg').textContent, /Wallet service is not ready/);
    assert.equal(harness.nodes.get('generate-deposit-addr-btn').disabled, false);
    assert.equal(harness.nodes.get('deposit-details-box').style.display, 'block');
    assert.equal(harness.nodes.get('deposit-full-addr').innerText, 'synthetic-pool-address');
    assert.equal(harness.nodes.get('copy-addr-btn').disabled, true);
});
