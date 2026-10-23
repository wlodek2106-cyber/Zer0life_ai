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
                    amountSol: Number(body.costSol),
                    payUri: `solana:merchant?amount=${body.costSol}&reference=unique` };
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
    assert.match(html, /id="boots-pending-invoices"/);
    assert.match(html, /id="boots-pending-invoice-list"/);
    assert.match(html, /id="hide-pending-boots-invoices"/);
    assert.match(html, /id="boots-hidden-invoices"/);
    assert.match(html, /id="boots-hidden-invoice-list"/);
    assert.match(html, /id="boots-pay-qr"/);
    assert.match(html, /id="boots-pay-recipient"/);
    assert.match(html, /id="copy-boots-merchant-address"[^>]*>Copy merchant wallet address/);
    assert.match(html, /id="copy-boots-pay-link"[^>]*>Copy Solana Pay request/);
    assert.match(html, /Transaction signature \(Tx\) from the wallet — not the address/);
    assert.match(html, /transaction signature \(Tx\), not the wallet address/);
    assert.match(html, /The address alone does not include this purchase.s reference and cannot activate Boots/);
    assert.match(html, /id="open-boots-wallet-pay"/);
    assert.match(html, /Open in Solflare/);
    assert.match(html, /id="boots-tx-signature-input"/);
    assert.equal((html.match(/<button class="booster-price-btn" type="button"/g) || []).length, 5);
    assert.doesNotMatch(html, /<div class="booster-price-btn"/);
    for (const price of ['0.2', '0.5', '1', '5', '10']) {
        assert.match(html, new RegExp(`onclick="buy(?:Timed)?Booster\\(${price.replace('.', '\\.')}`));
    }
    assert.match(purchaseFlow, /checkout\.hidden = false/);
    assert.match(purchaseFlow, /Preparing a secure Solana Pay invoice/);
    assert.ok(purchaseFlow.indexOf('checkout.hidden = false') < purchaseFlow.indexOf('await window.secureWallet.refresh()'));
    const beforePrepareRequest = purchaseFlow.slice(0, purchaseFlow.indexOf('const invoice = await window.secureWallet.request'));
    assert.doesNotMatch(beforePrepareRequest, /await window\.secureWallet\.refresh\(\)/);
    assert.match(checkout, /\/wallet\/boots\/payments\/prepare/);
    assert.match(checkout, /\/wallet\/boots\/payments\/verify/);
    assert.match(checkout, /function createSolflarePayLink\(payUri\)/);
    assert.match(checkout, /walletLink\.href = createSolflarePayLink\(invoice\.payUri\)/);
    assert.match(checkout, /https:\/\/solflare\.com\/ul\/v1\/browse\//);
    assert.match(checkout, /new URL\('wallet-pay\.html', window\.location\.href\)/);
    assert.doesNotMatch(checkout, /phantom\.com|Open in Phantom/);
    assert.match(checkout, /boots-pay-recipient'\)\.textContent = invoice\.recipient/);
    assert.match(checkout, /copy-boots-merchant-address'\)\.addEventListener\('click'[\s\S]*writeText\(recipient\)/);
    assert.match(checkout, /copy-boots-pay-link'\)\.addEventListener\('click'[\s\S]*writeText\(activeBootsPayment\.payUri\)/);
    assert.match(checkout, /open-boots-wallet-pay'\)\.addEventListener\('click', \(event\)[\s\S]*telegram\.openLink\(solflareLink\)/);
    assert.match(checkout, /window\.location\.assign\(solflareLink\)/);
    const solflareBridge = fs.readFileSync(path.join(__dirname, '../wallet-pay.html'), 'utf8');
    assert.match(solflareBridge, /request\.startsWith\('solana:'\)/);
    assert.match(solflareBridge, /reference/);
    assert.match(solflareBridge, /Open payment in Solflare/);
    assert.match(solflareBridge, /openButton\.href = request/);
    assert.match(solflareBridge, /openButton\.target = '_self'/);
    assert.doesNotMatch(solflareBridge, /window\.location\.assign\(request\)/);
    const bridgeBeforeUserClick = solflareBridge.slice(
        0,
        solflareBridge.indexOf("openButton.addEventListener('click'"),
    );
    assert.doesNotMatch(bridgeBeforeUserClick, /setTimeout|window\.location\.assign/);
    assert.doesNotMatch(solflareBridge, /Phantom|phantom\.com/);
    assert.match(checkout, /txSignature === activeBootsPayment\.recipient \|\| txSignature\.startsWith\('solana:'\)/);
    assert.match(checkout, /This is the merchant address or payment request, not a Tx signature/);
    assert.match(checkout, /result\.status !== 'confirmed'/);
    assert.match(checkout, /pendingBootsPayments/);
    assert.match(checkout, /function renderPendingBootsInvoices/);
    assert.match(checkout, /async function confirmHidePendingBootsInvoices/);
    assert.match(checkout, /\/wallet\/boots\/payments\/hide-pending/);
    assert.match(checkout, /\/wallet\/boots\/payments\/restore/);
    assert.match(checkout, /function renderHiddenBootsInvoices/);
    assert.match(html, /Hiding does not cancel a Solana Pay request/);
    assert.match(html, /Hidden requests are still payable/);
    assert.match(checkout, /This does not cancel or revoke the Solana Pay requests/);
    assert.match(checkout, /Each open invoice remains payable/);
    assert.match(checkout, /formatBootsSolAmount\(invoice\.amountSol\)/);
    assert.match(checkout, /pending\.find\(invoice => invoice\.purchaseId === savedPurchaseId\)/);
    assert.match(checkout, /invoice\.pendingBootsPayments/);
    const restoreFlow = checkout.slice(
        checkout.indexOf('async function restorePendingBootsPayment'),
        checkout.indexOf('async function purchaseVerifiedBooster'),
    );
    assert.match(restoreFlow, /window\.secureWallet\.getState\(\)/);
    assert.match(restoreFlow, /if \(!refreshFromServer\) return/);
    assert.match(restoreFlow, /await window\.secureWallet\.refresh\(\)/);
    assert.match(restoreFlow, /selectionVersion !== bootsPaymentSelectionVersion/);
    assert.match(purchaseFlow, /pendingBootsPayments: invoice\.pendingBootsPayments/);
    assert.match(checkout, /do not pay again/i);
    assert.doesNotMatch(checkout, /secureWallet\.spend\(['"]booster/);
    assert.match(html, /Your in-game SOL balance will not be charged/);
    assert.match(depositPanel, /id="copy-addr-btn"[^>]*>📋 Copy wallet address/);
    assert.match(depositPanel, /id="copy-deposit-pay-link"[^>]*>Copy Solana Pay request/);
    assert.match(depositPanel, /copied address alone omits your unique reference/i);
    assert.match(depositFlow, /copyAddressButton\.onclick[\s\S]*copyDepositText\(address\)/);
    assert.match(depositFlow, /copyDepositPayLinkButton\.onclick[\s\S]*copyDepositText\(activeDepositPayUri\)/);
});

test('Solflare universal link preserves the exact invoice URI and unique reference', () => {
    const html = fs.readFileSync(path.join(__dirname, '../dashboard.html'), 'utf8');
    const checkout = html.slice(html.indexOf('let boosterPurchaseBusy'), html.indexOf('window.openDaoModal'));
    const helper = checkout.match(/function createSolflarePayLink\(payUri\) \{[\s\S]*?\n        \}/);
    assert.ok(helper, 'Solflare Universal Link builder exists');

    const request = 'solana:11111111111111111111111111111111?amount=0.2&reference=11111111111111111111111111111111&label=Zer0Life+Run&message=Boots+1500+ZRL%2Fkm';
    const context = {
        window: {
            location: {
                href: 'https://wlodek2106-cyber.github.io/Zer0life_ai/dashboard.html?build=test',
            },
        },
        URL,
        encodeURIComponent,
        paymentRequest: request,
    };
    vm.runInNewContext(
        `${helper[0]}\nglobalThis.generatedLink = createSolflarePayLink(paymentRequest);`,
        context,
    );

    const universalLink = new URL(context.generatedLink);
    assert.equal(universalLink.origin, 'https://solflare.com');
    assert.ok(universalLink.pathname.startsWith('/ul/v1/browse/'));
    assert.equal(universalLink.searchParams.get('ref'), 'https://wlodek2106-cyber.github.io/Zer0life_ai/');
    const bridgeUrl = new URL(decodeURIComponent(universalLink.pathname.slice('/ul/v1/browse/'.length)));
    assert.equal(bridgeUrl.pathname, '/Zer0life_ai/wallet-pay.html');
    assert.equal(bridgeUrl.searchParams.get('request'), request);
    assert.equal(bridgeUrl.searchParams.get('build'), 'boots-solana-pay-tap-v5');
});
