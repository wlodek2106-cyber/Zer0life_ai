const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../wallet.js'), 'utf8');

async function fixture({ pendingPurchases = [] } = {}) {
    const calls = [];
    const account = {
        success: true,
        referralCode: 'TESTUSER',
        availableZrl: 25,
        gameSolBalance: 2,
        farms: [],
        boosterRate: 300,
        merchantPaymentsEnabled: false,
        bootsSolanaPayEnabled: true,
        pendingPurchases,
        pendingBootsPayments: [],
    };
    const window = {
        Telegram: { WebApp: { initData: 'synthetic', initDataUnsafe: { user: { id: 200 } } } },
        addEventListener() {},
        crypto: { randomUUID: () => 'new-purchase-id' },
    };
    const context = {
        window,
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        document: { getElementById: () => null },
        console: { warn() {} },
        setTimeout: (fn, delay) => { if (delay === 3000) setImmediate(fn); },
        async fetch(url, options) {
            const route = new URL(url).pathname;
            const body = JSON.parse(options.body);
            calls.push({ route, body });
            let result = { success: true };
            if (route === '/wallet/state') result = { ...account };
            if (route === '/wallet/boots/payments/prepare') {
                result = { success: true, purchaseId: body.purchaseId, status: 'pending',
                    amountSol: .2, payUri: 'solana:merchant?amount=0.2&reference=unique' };
            }
            if (route === '/wallet/boots/payments/verify') {
                result = { success: true, purchaseId: body.purchaseId, status: 'confirmed',
                    boosterRate: 1500, txHash: body.txSignature };
            }
            if (route === '/wallet/purchases/status') {
                result = { ...account, purchaseId: body.purchaseId, status: 'confirmed' };
            }
            return { ok: true, json: async () => result };
        },
    };
    vm.runInNewContext(source, context);
    await new Promise(resolve => setImmediate(resolve));
    calls.length = 0;
    return { api: window.secureWallet, calls, account };
}

test('legacy game-balance Boots spend is blocked without an API request', async () => {
    const f = await fixture();
    await assert.rejects(f.api.spend('booster', { costSol: .2 }), /Solana Pay/);
    assert.equal(f.calls.some(call => call.route === '/wallet/spend'), false);
    assert.equal(f.api.getState().gameSolBalance, 2);
});

test('Solana Pay invoice and Tx verification use authenticated Wallet API requests', async () => {
    const f = await fixture();
    const invoice = await f.api.request('/wallet/boots/payments/prepare', {
        purchaseId: 'new-purchase-id',
        costSol: .2,
    });
    assert.equal(invoice.status, 'pending');
    assert.match(invoice.payUri, /^solana:/);
    const verified = await f.api.request('/wallet/boots/payments/verify', {
        purchaseId: invoice.purchaseId,
        txSignature: 'synthetic-transaction-signature',
    });
    assert.equal(verified.status, 'confirmed');
    assert.equal(f.calls.length, 2);
    assert.equal(f.calls[0].route, '/wallet/boots/payments/prepare');
    assert.equal(f.calls[0].body.initData, 'synthetic');
    assert.equal(f.calls[1].route, '/wallet/boots/payments/verify');
    assert.equal(f.calls[1].body.txSignature, 'synthetic-transaction-signature');
});

test('legacy pending Merchant purchases remain eligible for recovery after new Boots payments switch', () => {
    const condition = source.slice(source.indexOf('if (!merchantTask'), source.indexOf('async function refresh'));
    assert.match(condition, /Array\.isArray\(nextState\.pendingPurchases\)/);
    assert.doesNotMatch(condition, /merchantPaymentsEnabled/);
});

test('Boots store exposes working price buttons and a visible Solana Pay checkout', () => {
    const html = fs.readFileSync(path.join(__dirname, '../dashboard.html'), 'utf8');
    const checkout = html.slice(html.indexOf('let boosterPurchaseBusy'), html.indexOf('window.openDaoModal'));
    const depositPanel = html.slice(html.indexOf('<!-- DEPOSIT MODAL -->'), html.indexOf('<!-- BOOSTERS MODAL -->'));
    const depositFlow = html.slice(html.indexOf('async function copyDepositText'), html.indexOf("document.getElementById('check-deposit-status-btn').onclick"));
    const purchaseFlow = checkout.slice(checkout.indexOf('async function purchaseVerifiedBooster'));
    assert.match(html, /id="boots-solana-pay-checkout"/);
    assert.match(html, /id="boots-pay-qr"/);
    assert.match(html, /id="boots-pay-recipient"/);
    assert.match(html, /id="copy-boots-merchant-address"[^>]*>Copy merchant wallet address/);
    assert.match(html, /id="copy-boots-pay-link"[^>]*>Copy Solana Pay request/);
    assert.match(html, /Transaction signature \(Tx\) from the wallet — not the address/);
    assert.match(html, /transaction signature \(Tx\), not the wallet address/);
    assert.match(html, /The address alone does not include this purchase.s reference and cannot activate Boots/);
    assert.match(html, /id="open-boots-wallet-pay"/);
    assert.match(html, /id="boots-tx-signature-input"/);
    assert.equal((html.match(/<button class="booster-price-btn" type="button"/g) || []).length, 5);
    assert.doesNotMatch(html, /<div class="booster-price-btn"/);
    assert.match(html, /onclick="buyTimedBooster\(0\.2/);
    assert.match(html, /onclick="buyBooster\(0\.5/);
    assert.match(purchaseFlow, /checkout\.hidden = false/);
    assert.match(purchaseFlow, /Preparing a secure Solana Pay invoice/);
    assert.ok(purchaseFlow.indexOf('checkout.hidden = false') < purchaseFlow.indexOf('await window.secureWallet.refresh()'));
    assert.match(checkout, /\/wallet\/boots\/payments\/prepare/);
    assert.match(checkout, /\/wallet\/boots\/payments\/verify/);
    assert.match(checkout, /walletLink\.href = invoice\.payUri/);
    assert.match(checkout, /boots-pay-recipient'\)\.textContent = invoice\.recipient/);
    assert.match(checkout, /copy-boots-merchant-address'\)\.addEventListener\('click'[\s\S]*writeText\(recipient\)/);
    assert.match(checkout, /copy-boots-pay-link'\)\.addEventListener\('click'[\s\S]*writeText\(activeBootsPayment\.payUri\)/);
    assert.match(checkout, /txSignature === activeBootsPayment\.recipient \|\| txSignature\.startsWith\('solana:'\)/);
    assert.match(checkout, /This is the merchant address or payment request, not a Tx signature/);
    assert.match(checkout, /result\.status !== 'confirmed'/);
    assert.match(checkout, /pendingBootsPayments/);
    assert.match(checkout, /do not pay again/i);
    assert.doesNotMatch(checkout, /secureWallet\.spend\(['"]booster/);
    assert.match(html, /Your in-game SOL balance will not be charged/);
    assert.match(depositPanel, /id="copy-addr-btn"[^>]*>📋 Copy wallet address/);
    assert.match(depositPanel, /id="copy-deposit-pay-link"[^>]*>Copy Solana Pay request/);
    assert.match(depositPanel, /copied address alone omits your unique reference/i);
    assert.match(depositFlow, /copyAddressButton\.onclick[\s\S]*copyDepositText\(address\)/);
    assert.match(depositFlow, /copyDepositPayLinkButton\.onclick[\s\S]*copyDepositText\(activeDepositPayUri\)/);
});
