const TELEGRAM_ERROR = 'Open Zer0Life Run inside Telegram. Wallet sign-in is not complete yet.';
const ACCOUNT_ERROR = 'The wallet account does not match your Telegram profile. Sign out of the wallet and reopen the mini app.';
const ADDRESS_ERROR = 'A different address is already linked to this profile. Automatic replacement is disabled; balances are safe.';

export function createWalletController({ onChange = () => {}, timeoutMs = 30000 } = {}) {
    let sdk = {};
    let creation = null;
    let disposed = false;
    const listeners = new Set();

    function wallet() {
        const owned = new Set((sdk.user?.linkedAccounts || [])
            .filter(account => account.type === 'wallet' && account.chainType === 'solana'
                && ['privy', 'privy-v2'].includes(account.walletClientType))
            .map(account => account.address));
        return (sdk.wallets || []).find(item => owned.has(item.address)
            && item.standardWallet?.isPrivyWallet === true);
    }

    function getStatus() {
        return {
            ready: Boolean(sdk.ready && sdk.authenticated && sdk.walletsReady),
            authenticated: Boolean(sdk.authenticated),
            telegramId: sdk.user?.telegram?.telegramUserId || '',
            address: wallet()?.address || '',
        };
    }

    function assertIdentity(expectedId) {
        if (!expectedId) throw new Error('Load the verified profile from the server first.');
        if (!sdk.ready || !sdk.authenticated || !sdk.walletsReady) throw new Error(TELEGRAM_ERROR);
        if (String(sdk.user?.telegram?.telegramUserId || '') !== String(expectedId)) {
            throw new Error(ACCOUNT_ERROR);
        }
    }

    function waitUntil(predicate) {
        if (disposed) return Promise.reject(new Error('The wallet is closed.'));
        if (predicate()) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => finish(new Error(TELEGRAM_ERROR)), timeoutMs);
            const check = () => {
                if (disposed) finish(new Error('The wallet is closed.'));
                else if (predicate()) finish();
            };
            function finish(error) {
                clearTimeout(timer);
                listeners.delete(check);
                if (error) reject(error);
                else resolve();
            }
            listeners.add(check);
        });
    }

    async function readyFor(expectedId) {
        if (!expectedId) throw new Error('Load the verified profile from the server first.');
        if (sdk.ready && sdk.authenticated
            && String(sdk.user?.telegram?.telegramUserId || '') !== String(expectedId)) {
            throw new Error(ACCOUNT_ERROR);
        }
        await waitUntil(() => Boolean(sdk.ready && sdk.authenticated && sdk.walletsReady));
        assertIdentity(expectedId);
    }

    async function connect({ telegramId, expectedAddress = '' }) {
        await readyFor(telegramId);
        if (expectedAddress && wallet()?.address !== expectedAddress) throw new Error(ADDRESS_ERROR);
        if (!wallet()) {
            if (!creation) {
                creation = (async () => {
                    assertIdentity(telegramId);
                    await sdk.createWallet({ createAdditional: false });
                    await waitUntil(() => Boolean(wallet()) ||
                        String(sdk.user?.telegram?.telegramUserId || '') !== String(telegramId));
                    assertIdentity(telegramId);
                })().finally(() => { creation = null; });
            }
            await creation;
        }
        assertIdentity(telegramId);
        const address = wallet()?.address;
        if (!address || (expectedAddress && address !== expectedAddress)) throw new Error(ADDRESS_ERROR);
        return address;
    }

    async function signMessage({ message, address, telegramId, title = 'Approve signature' }) {
        await readyFor(telegramId);
        const signer = wallet();
        if (!address || signer?.address !== address) throw new Error(ADDRESS_ERROR);
        const result = await sdk.signMessage({
            wallet: signer,
            message: new TextEncoder().encode(message),
            options: { uiOptions: { showWalletUIs: true, title, isCancellable: true } },
        });
        assertIdentity(telegramId);
        if (wallet()?.address !== address || result?.signature?.length !== 64) {
            throw new Error('The address changed or no signature was received. No transfer was sent.');
        }
        return result.signature;
    }

    return {
        getStatus,
        connect,
        signMessage,
        async exportWallet({ address, telegramId }) {
            await readyFor(telegramId);
            if (!address || wallet()?.address !== address) throw new Error(ADDRESS_ERROR);
            await sdk.exportWallet({ address });
        },
        async resetSession() {
            if (creation) throw new Error('Wait for wallet creation to finish.');
            if (typeof sdk.logout !== 'function') throw new Error('The sign-in service is still loading.');
            await sdk.logout();
        },
        update(next) {
            sdk = next;
            for (const listener of [...listeners]) listener();
            onChange(getStatus());
        },
        dispose() {
            disposed = true;
            for (const listener of [...listeners]) listener();
        },
    };
}
