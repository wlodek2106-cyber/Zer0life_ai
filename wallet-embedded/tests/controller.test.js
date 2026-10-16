import test from 'node:test';
import assert from 'node:assert/strict';
import { createWalletController } from '../src/controller.js';

function fixture(id = '200', address = 'wallet-200') {
    const calls = [];
    const controller = createWalletController({ timeoutMs: 30 });
    const sdk = {
        ready: true, authenticated: true, walletsReady: true,
        user: { telegram: { telegramUserId: id }, linkedAccounts: [] },
        wallets: [],
        async createWallet(options) {
            calls.push(['create', options]);
            sdk.user = { ...sdk.user, linkedAccounts: [{
                type: 'wallet', chainType: 'solana', walletClientType: 'privy', address,
            }] };
            sdk.wallets = [{ address, standardWallet: { isPrivyWallet: true } }];
            controller.update({ ...sdk });
        },
        async signMessage(input) {
            calls.push(['sign', input]);
            return { signature: new Uint8Array(64).fill(7) };
        },
        async exportWallet(input) { calls.push(['export', input]); },
        async logout() { calls.push(['logout']); },
    };
    controller.update(sdk);
    return { controller, sdk, calls };
}

test('initializing an account creates no wallet and signs nothing', () => {
    const f = fixture();
    assert.deepEqual(f.calls, []);
    assert.equal(f.controller.getStatus().telegramId, '200');
    assert.equal(f.controller.getStatus().address, '');
});

test('simultaneous creation requests create one personal wallet, and reload reuses it', async () => {
    const f = fixture();
    assert.deepEqual(await Promise.all([
        f.controller.connect({ telegramId: '200' }),
        f.controller.connect({ telegramId: '200' }),
    ]), ['wallet-200', 'wallet-200']);
    await f.controller.connect({ telegramId: '200', expectedAddress: 'wallet-200' });
    assert.equal(f.calls.filter(call => call[0] === 'create').length, 1);
    assert.deepEqual(f.calls[0][1], { createAdditional: false });
});

test('two Telegram users receive distinct wallets, with no shared singleton address', async () => {
    const a = fixture('200', 'wallet-200');
    const b = fixture('201', 'wallet-201');
    assert.notEqual(await a.controller.connect({ telegramId: '200' }),
        await b.controller.connect({ telegramId: '201' }));
    await assert.rejects(a.controller.connect({ telegramId: '201' }), /does not match/);
    await assert.rejects(a.controller.signMessage({
        telegramId: '201', address: 'wallet-200', message: 'never sign for another player',
    }), /does not match/);
    assert(!a.calls.some(call => call[0] === 'sign'));
});

test('an existing different bound address cannot be replaced, even by creating a new wallet', async () => {
    const f = fixture();
    await assert.rejects(f.controller.connect({
        telegramId: '200', expectedAddress: 'old-external-wallet',
    }), /replacement is disabled/);
    assert.equal(f.calls.length, 0);
});

test('signs the exact server challenge, visibly and only with the matching embedded wallet', async () => {
    const f = fixture();
    await f.controller.connect({ telegramId: '200' });
    const challenge = 'Zer0Life Run\nwithdraw_sol\nnonce-only-for-200\n0.001';
    const signature = await f.controller.signMessage({
        message: challenge, address: 'wallet-200', telegramId: '200',
    });
    assert.equal(signature.length, 64);
    const input = f.calls.find(call => call[0] === 'sign')[1];
    assert.equal(new TextDecoder().decode(input.message), challenge);
    assert.equal(input.options.uiOptions.showWalletUIs, true);
    await assert.rejects(f.controller.signMessage({
        message: challenge, address: 'someone-else', telegramId: '200',
    }), /replacement is disabled/);
});

test('changing Telegram account during a signature rejects its result', async () => {
    const f = fixture();
    await f.controller.connect({ telegramId: '200' });
    f.sdk.signMessage = async () => {
        f.controller.update({ ...f.sdk, user: { telegram: { telegramUserId: '201' }, linkedAccounts: [] } });
        return { signature: new Uint8Array(64) };
    };
    f.controller.update({ ...f.sdk });
    await assert.rejects(f.controller.signMessage({
        message: 'never submit this', address: 'wallet-200', telegramId: '200',
    }), /does not match/);
});

test('cancellation or malformed SDK signatures are never returned as valid proof', async () => {
    const f = fixture();
    await f.controller.connect({ telegramId: '200' });
    f.sdk.signMessage = async () => { throw new Error('User cancelled'); };
    f.controller.update({ ...f.sdk });
    await assert.rejects(f.controller.signMessage({
        message: 'cancel', address: 'wallet-200', telegramId: '200',
    }), /cancelled/);
    f.sdk.signMessage = async () => ({ signature: new Uint8Array(3) });
    f.controller.update({ ...f.sdk });
    await assert.rejects(f.controller.signMessage({
        message: 'bad', address: 'wallet-200', telegramId: '200',
    }), /no signature was received/);
});

test('wallet discovery ignores injected external wallets and wallets absent from the Privy user', () => {
    const f = fixture();
    f.controller.update({ ...f.sdk, wallets: [
        { address: 'unowned', standardWallet: { isPrivyWallet: true } },
        { address: 'external', standardWallet: { isPrivyWallet: false } },
    ] });
    assert.equal(f.controller.getStatus().address, '');
});

test('a missing Telegram session or unverified server identity cannot create a wallet', async () => {
    const f = fixture();
    await assert.rejects(f.controller.connect({ telegramId: '' }), /profile/);
    f.controller.update({ ...f.sdk, authenticated: false });
    await assert.rejects(f.controller.connect({ telegramId: '200' }), /Telegram/);
    assert.equal(f.calls.length, 0);
});

test('export only opens the provider recovery UI for the current player and exact address', async () => {
    const f = fixture();
    await f.controller.connect({ telegramId: '200' });
    await f.controller.exportWallet({ telegramId: '200', address: 'wallet-200' });
    assert.deepEqual(f.calls.at(-1), ['export', { address: 'wallet-200' }]);
    await assert.rejects(f.controller.exportWallet({ telegramId: '201', address: 'wallet-200' }), /does not match/);
    await assert.rejects(f.controller.exportWallet({ telegramId: '200', address: 'other' }), /replacement is disabled/);
});
