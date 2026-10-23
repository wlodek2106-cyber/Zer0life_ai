const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../dashboard.html'), 'utf8');
const statusHelper = html.match(/            const setDepositStatus = \(message\) => \{[\s\S]*?\n            \};/);
const activeReferenceDeclaration = html.match(/            let activeDepositReference = '';\n            let activeDepositPayUri = '';/);
const addressHandler = html.match(/            const addressButton = document\.getElementById\('generate-deposit-addr-btn'\);\n            addressButton\.onclick = async \(\) => \{[\s\S]*?\n            \};/);
const copyHelper = html.match(/            async function copyDepositText\(value\) \{[\s\S]*?\n            \}/);
const copyAddressHandler = html.match(/            const copyAddressButton = document\.getElementById\('copy-addr-btn'\);\n            copyAddressButton\.onclick = async \(\) => \{[\s\S]*?\n            \};/);
const copyPayLinkHandler = html.match(/            const copyDepositPayLinkButton = document\.getElementById\('copy-deposit-pay-link'\);\n            copyDepositPayLinkButton\.onclick = async \(\) => \{[\s\S]*?\n            \};/);
const verifyHandler = html.match(/            document\.getElementById\('check-deposit-status-btn'\)\.onclick = async \(\) => \{[\s\S]*?\n            \};/);

assert(statusHelper, 'Deposit status helper must exist');
assert(activeReferenceDeclaration, 'Deposit reference state must exist');
assert(addressHandler, 'Deposit address handler must exist');
assert(copyHelper, 'Deposit copy helper must exist');
assert(copyAddressHandler, 'Wallet address copy handler must exist');
assert(copyPayLinkHandler, 'Solana Pay request copy handler must exist');
assert(verifyHandler, 'Deposit Tx verification handler must exist');

function createHarness(responses = []) {
    const requests = [];
    const nodes = new Map([
        ['deposit-status-msg', { textContent: '', hidden: true }],
        ['deposit-details-box', { style: { display: 'none' } }],
        ['generate-deposit-addr-btn', { disabled: false, textContent: 'Show Deposit Address 🚀' }],
        ['deposit-full-addr', { innerText: '' }],
        ['deposit-qr-img', { src: '', removeAttribute(name) { if (name === 'src') this.src = ''; } }],
        ['check-deposit-status-btn', { disabled: false }],
        ['copy-addr-btn', { disabled: false }],
        ['copy-deposit-pay-link', { disabled: false }],
        ['tx-signature-input', { disabled: false }],
    ]);
    const context = {
        document: { getElementById: id => nodes.get(id) || null },
        window: {
            Telegram: { WebApp: { initData: 'synthetic-telegram-init-data' } },
            secureWallet: { async refresh() {} },
        },
        navigator: { clipboard: { async writeText(value) { context.copiedText = value; } } },
        SHARED_POOL_WALLET: 'synthetic-pool-address',
        fetch: async (...args) => {
            requests.push(args);
            const response = responses.shift();
            if (response instanceof Error) throw response;
            if (!response) throw new Error('No synthetic response configured');
            return response;
        },
        depositModal: { style: { display: 'flex' } },
        tg: { showAlert() {} },
        showLiveTxToast() {},
        simulatedDepositorsCount: 0,
        simulatedTotalSolIn: 0,
        username: 'Synthetic player',
        console: { warn() {} },
        triggerHaptic() {},
        playSound() {},
        encodeURIComponent,
    };
    vm.runInNewContext(
        `${statusHelper[0]}\n${activeReferenceDeclaration[0]}\n${addressHandler[0]}\n${copyHelper[0]}\n${copyAddressHandler[0]}\n${copyPayLinkHandler[0]}\n${verifyHandler[0]}\nglobalThis.readPayUri = () => activeDepositPayUri;`,
        context,
    );
    return {
        nodes,
        requests,
        context,
        click: () => nodes.get('generate-deposit-addr-btn').onclick(),
        copyAddress: () => nodes.get('copy-addr-btn').onclick(),
        copyPayLink: () => nodes.get('copy-deposit-pay-link').onclick(),
        verify: () => nodes.get('check-deposit-status-btn').onclick(),
    };
}

test('deposit status is visible outside the hidden deposit details area', () => {
    const statusStart = html.indexOf('<div id="deposit-status-msg"');
    const detailsStart = html.indexOf('<div id="deposit-details-box"');
    assert(statusStart >= 0 && statusStart < detailsStart);
    assert.match(html, /id="deposit-status-msg" role="status" aria-live="polite" hidden/);
    assert.doesNotMatch(html, /deposit-source-warning|verified sender address is required|Transfers cannot be credited/i);
    assert.match(html, /Scan the QR to deposit/);
    assert.doesNotMatch(html, /Unique deposit reference \(required\)|copy-deposit-reference-btn|Copy Deposit Reference/);
    assert.match(html, /id="copy-addr-btn"[^>]*>📋 Copy wallet address/);
    assert.match(html, /id="copy-deposit-pay-link"[^>]*>Copy Solana Pay request/);
    assert.match(html, /Sending to the copied address alone omits your unique reference/);
});

test('deposit details show the QR and address without displaying the unique reference', async () => {
    const harness = createHarness([{
        ok: true,
        async json() {
            return {
                success: true,
                address: 'synthetic-pool-address',
                reference: 'synthetic-deposit-reference',
            };
        },
    }]);
    await harness.click();

    const request = harness.requests[0];
    assert.match(request[0], /\/wallet\/deposit\/reference$/);
    assert.equal(request[1].method, 'POST');
    assert.equal(JSON.parse(request[1].body).initData, 'synthetic-telegram-init-data');
    assert.equal(harness.nodes.get('deposit-status-msg').hidden, true);
    assert.equal(harness.nodes.get('deposit-details-box').style.display, 'block');
    assert.equal(harness.nodes.get('deposit-full-addr').innerText, 'synthetic-pool-address');
    const qrData = new URL(harness.nodes.get('deposit-qr-img').src).searchParams.get('data');
    assert.match(qrData, /^solana:synthetic-pool-address\?/);
    assert.match(qrData, /reference=synthetic-deposit-reference/);
    assert.equal(harness.context.readPayUri(), qrData);
    assert.equal(harness.nodes.get('check-deposit-status-btn').disabled, false);
    assert.equal(harness.nodes.get('copy-addr-btn').disabled, false);
    assert.equal(harness.nodes.get('copy-deposit-pay-link').disabled, false);
    assert.equal(harness.nodes.get('tx-signature-input').disabled, false);
    assert.equal(harness.nodes.get('generate-deposit-addr-btn').disabled, false);
    await harness.copyAddress();
    assert.equal(harness.context.copiedText, 'synthetic-pool-address');
    await harness.copyPayLink();
    assert.equal(harness.context.copiedText, qrData);
});

test('reference preparation failures remain visible and do not enable transfer verification', async () => {
    const harness = createHarness([{
        ok: false,
        async json() {
            return { success: false, error: 'Deposit API unavailable' };
        },
    }]);
    await harness.click();

    assert.equal(harness.nodes.get('deposit-status-msg').hidden, false);
    assert.match(harness.nodes.get('deposit-status-msg').textContent, /Deposit API unavailable/);
    assert.equal(harness.nodes.get('generate-deposit-addr-btn').disabled, false);
    assert.equal(harness.nodes.get('deposit-details-box').style.display, 'block');
    assert.equal(harness.nodes.get('deposit-full-addr').innerText, 'synthetic-pool-address');
    assert.equal(harness.nodes.get('check-deposit-status-btn').disabled, true);
    assert.equal(harness.nodes.get('copy-addr-btn').disabled, true);
    assert.equal(harness.nodes.get('copy-deposit-pay-link').disabled, true);
    assert.equal(harness.nodes.get('tx-signature-input').disabled, true);
});

test('network failure is visible and leaves deposit actions disabled', async () => {
    const harness = createHarness([new Error('Network unavailable')]);
    await harness.click();

    assert.equal(harness.nodes.get('deposit-status-msg').hidden, false);
    assert.match(harness.nodes.get('deposit-status-msg').textContent, /Network unavailable/);
    assert.equal(harness.nodes.get('generate-deposit-addr-btn').disabled, false);
    assert.equal(harness.nodes.get('deposit-details-box').style.display, 'block');
    assert.equal(harness.nodes.get('deposit-full-addr').innerText, 'synthetic-pool-address');
    assert.equal(harness.nodes.get('copy-addr-btn').disabled, true);
    assert.equal(harness.nodes.get('copy-deposit-pay-link').disabled, true);
    assert.equal(harness.nodes.get('check-deposit-status-btn').disabled, true);
});

test('Tx verification sends the active unique reference to the server', async () => {
    const harness = createHarness([
        {
            ok: true,
            async json() {
                return {
                    success: true,
                    address: 'synthetic-pool-address',
                    reference: 'synthetic-deposit-reference',
                };
            },
        },
        {
            ok: true,
            async json() {
                return { success: true, depositedSOL: 1, gameSolBalance: 1 };
            },
        },
    ]);
    await harness.click();
    harness.nodes.get('tx-signature-input').value = 'synthetic-solana-signature';
    await harness.verify();

    const request = harness.requests[1];
    assert.match(request[0], /\/wallet\/deposit\/verify$/);
    assert.equal(request[1].method, 'POST');
    assert.deepEqual(JSON.parse(request[1].body), {
        initData: 'synthetic-telegram-init-data',
        txSignature: 'synthetic-solana-signature',
        depositReference: 'synthetic-deposit-reference',
    });
    assert.equal(harness.context.depositModal.style.display, 'none');
    assert.equal(harness.nodes.get('check-deposit-status-btn').disabled, false);
});
