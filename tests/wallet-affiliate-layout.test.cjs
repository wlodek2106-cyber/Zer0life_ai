'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'dashboard.html'), 'utf8');
const walletJs = fs.readFileSync(path.join(root, 'wallet.js'), 'utf8');

function createNode() {
    return {
        textContent: '',
        value: '',
        style: {},
        children: [],
        events: {},
        addEventListener(name, handler) {
            this.events[name] = handler;
        },
        replaceChildren(...children) {
            this.children = children;
        },
        append(...children) {
            this.children.push(...children);
        },
        appendChild(child) {
            this.children.push(child);
        },
        focus() {},
        select() {},
    };
}

function createWallet() {
    const ids = [
        'affiliate-code',
        'affiliate-link',
        'affiliate-levels',
        'affiliate-active-total',
        'affiliate-active-unit',
        'affiliate-earned-total',
        'affiliate-copy-btn',
        'asset-zrl-bal',
        'asset-zrl-usd',
        'asset-sol-bal',
        'asset-sol-usd',
    ];
    const nodes = new Map(ids.map((id) => [id, createNode()]));
    const fixture = {
        success: true,
        telegramId: 'synthetic-affiliate-ui-user',
        withdrawalMode: 'trusted_destination_v2',
        referralCode: 'ABCDEF1234',
        availableZrl: 54699.64,
        gameSolBalance: 0.357992908,
        pendingRunZrl: 0,
        affiliateLevels: [
            { level: 1, ratePercent: 10, users: 2, earnedZrl: 1763 },
            { level: 2, ratePercent: 4, users: 3, earnedZrl: 40 },
            { level: 3, ratePercent: 3, users: 1, earnedZrl: 30 },
            { level: 4, ratePercent: 2, users: 0, earnedZrl: 20 },
            { level: 5, ratePercent: 1, users: 1, earnedZrl: 10 },
        ],
        pendingWithdrawalApprovals: [],
        pendingWithdrawals: [],
        pendingPurchases: [],
    };
    const storage = new Map();
    const copiedLinks = [];
    const window = {
        Telegram: { WebApp: { initData: 'synthetic-session' } },
        prompt: () => {},
    };
    const context = {
        window,
        document: {
            getElementById: (id) => nodes.get(id) || null,
            createElement: createNode,
        },
        localStorage: {
            getItem: (key) => storage.get(key) || null,
            setItem: (key, value) => storage.set(key, value),
        },
        navigator: { clipboard: { writeText: async (text) => copiedLinks.push(text) } },
        setTimeout: () => 1,
        clearTimeout: () => {},
        fetch: async (url) => ({
            ok: true,
            status: 200,
            json: async () => url.endsWith('/wallet/state') ? fixture : { success: true },
        }),
        console,
        URL,
    };
    vm.runInNewContext(walletJs, context);
    return { nodes, fixture, wallet: window.secureWallet, copiedLinks };
}

function flattenedText(node) {
    return [node.textContent || '', ...node.children.flatMap(flattenedText)].join(' ');
}

test('Wallet places withdrawable balances before the partner program', () => {
    const body = html.slice(
        html.indexOf('<div id="wallet-modal-body">'),
        html.indexOf('<section id="recovery-admin-panel">'),
    );
    const balances = body.indexOf('id="wallet-balances-panel"');
    const actions = body.indexOf('id="wallet-actions-div"');
    const partners = body.indexOf('id="affiliate-panel"');

    assert.notEqual(balances, -1);
    assert.notEqual(partners, -1);
    assert.notEqual(actions, -1);
    assert(balances < actions, 'balances must be visible before the wallet actions');
    assert(actions < partners, 'wallet actions should stay beside the balances, before partner details');
    for (const id of ['asset-zrl-bal', 'asset-sol-bal', 'affiliate-link', 'affiliate-copy-btn']) {
        assert.match(body, new RegExp(`id="${id}"`));
    }
});

test('Wallet renders all five live partner levels and accurate aggregate totals', async () => {
    const { nodes, wallet, copiedLinks } = createWallet();
    await wallet.refresh();

    const referralLink = 'https://t.me/Zer0lifelabs_ai_bot?start=ref_ABCDEF1234';
    assert.equal(nodes.get('affiliate-code').textContent, 'ABCDEF1234');
    assert.equal(nodes.get('affiliate-link').value, referralLink);
    assert.equal(nodes.get('affiliate-levels').children.length, 5);
    assert.equal(nodes.get('affiliate-active-total').textContent, '7');
    assert.equal(nodes.get('affiliate-active-unit').textContent, 'players');
    assert.equal(nodes.get('affiliate-earned-total').textContent, '1,863');

    const expected = [
        ['01', 'Direct', '10%', '2', '1,763 ZRL'],
        ['02', 'Second level', '4%', '3', '40 ZRL'],
        ['03', 'Third level', '3%', '1', '30 ZRL'],
        ['04', 'Fourth level', '2%', '0', '20 ZRL'],
        ['05', 'Fifth level', '1%', '1', '10 ZRL'],
    ];
    nodes.get('affiliate-levels').children.forEach((row, index) => {
        const text = flattenedText(row);
        for (const value of expected[index]) assert(text.includes(value), `level ${index + 1} should show ${value}`);
        assert(text.includes(expected[index][3] === '1' ? 'player' : 'players'));
    });

    await nodes.get('affiliate-copy-btn').events.click();
    assert.deepEqual(copiedLinks, [referralLink]);
});

test('Affiliate rows are replaced on refresh rather than duplicated', async () => {
    const { nodes, fixture, wallet } = createWallet();
    await wallet.refresh();
    fixture.affiliateLevels = fixture.affiliateLevels.slice(0, 2);
    await wallet.refresh();

    assert.equal(nodes.get('affiliate-levels').children.length, 2);
    assert.equal(nodes.get('affiliate-active-total').textContent, '5');
    assert.equal(nodes.get('affiliate-earned-total').textContent, '1,803');
});
