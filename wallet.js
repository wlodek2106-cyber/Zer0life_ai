(() => {
    const API_BASE = 'https://zer0life-ai-iz5n.onrender.com';
    const telegramApp = window.Telegram?.WebApp;
    const initData = () => telegramApp?.initData || '';
    const byId = (id) => document.getElementById(id);
    let state = null;
    let busy = false;

    async function post(path, payload = {}) {
        const response = await fetch(`${API_BASE}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            cache: 'no-store',
            body: JSON.stringify({ initData: initData(), ...payload }),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success) {
            throw new Error(result.error || `Wallet request failed (${response.status})`);
        }
        return result;
    }

    function alertUser(message) {
        if (telegramApp?.showAlert) telegramApp.showAlert(message);
        else window.alert(message);
    }

    function toBase64(bytes) {
        let binary = '';
        new Uint8Array(bytes).forEach((byte) => { binary += String.fromCharCode(byte); });
        return btoa(binary);
    }

    function provider() {
        return window.phantom?.solana || (window.solana?.isPhantom ? window.solana : null);
    }

    function preserveLegacyBalance() {
        if (typeof STORAGE_PREFIX === 'undefined') return { zrl: 0, sol: 0 };
        const key = STORAGE_PREFIX + 'legacy_pending_zrl';
        const solKey = STORAGE_PREFIX + 'legacy_pending_sol';
        const farmsKey = STORAGE_PREFIX + 'legacy_pending_farms';
        if (localStorage.getItem(farmsKey) === null) {
            localStorage.setItem(farmsKey, localStorage.getItem(STORAGE_PREFIX + 'active_farms') || '[]');
        }
        if (localStorage.getItem(key) === null || localStorage.getItem(solKey) === null) {
            const total = Number(localStorage.getItem(STORAGE_PREFIX + 'total_balance')) || 0;
            const earned = Number(localStorage.getItem(STORAGE_PREFIX + 'earned_zrl')) || 0;
            const unclaimed = Number(localStorage.getItem(STORAGE_PREFIX + 'unclaimed')) || 0;
            const legacy = Math.max(total, earned) + unclaimed;
            if (localStorage.getItem(key) === null) localStorage.setItem(key, String(Math.max(0, legacy)));
            const oldSol = Number(localStorage.getItem(STORAGE_PREFIX + 'pool_deposit')) || 0;
            const oldWon = Number(localStorage.getItem(STORAGE_PREFIX + 'casino_total_won')) || 0;
            const oldTurnover = Number(localStorage.getItem(STORAGE_PREFIX + 'casino_turnover')) || 0;
            if (localStorage.getItem(solKey) === null) {
                localStorage.setItem(solKey, String(Math.max(0, oldSol + oldWon - oldTurnover)));
            }
        }
        return {
            zrl: Number(localStorage.getItem(key)) || 0,
            sol: Number(localStorage.getItem(solKey)) || 0,
        };
    }

    function renderWallet(nextState) {
        state = nextState;
        const address = nextState.walletAddress || '';
        if (byId('phantom-link-label')) {
            byId('phantom-link-label').textContent = address ? 'Phantom connected and verified' : 'Phantom not connected';
        }
        if (byId('phantom-linked-address')) {
            byId('phantom-linked-address').textContent = address
                ? `${address.slice(0, 7)}…${address.slice(-7)}`
                : 'Link Phantom to view balances and withdraw';
        }
        if (byId('connect-phantom-btn')) {
            byId('connect-phantom-btn').textContent = address ? 'Phantom wallet linked' : 'Connect Phantom';
            byId('connect-phantom-btn').disabled = Boolean(address);
        }
        if (byId('asset-zrl-bal')) byId('asset-zrl-bal').textContent = Number(nextState.availableZrl || 0).toFixed(2);
        if (byId('asset-zrl-usd')) {
            byId('asset-zrl-usd').textContent = `Daily limit remaining: ${Number(nextState.dailyWithdrawalRemaining || 0).toFixed(2)} ZRL`;
        }
        if (byId('asset-sol-bal')) byId('asset-sol-bal').textContent = Number(nextState.gameSolBalance || 0).toFixed(4);
        if (byId('asset-sol-usd')) byId('asset-sol-usd').textContent = 'Verified deposit balance';
        if (byId('asset-phantom-sol')) {
            byId('asset-phantom-sol').textContent = nextState.solBalance == null
                ? (address ? 'Unavailable' : '—')
                : `${Number(nextState.solBalance).toFixed(4)} SOL`;
        }
        if (byId('asset-phantom-zrl')) {
            byId('asset-phantom-zrl').textContent = nextState.zrlBalance == null
                ? (address && !nextState.zrlConfigured ? 'Mint not configured' : (address ? 'Unavailable' : '—'))
                : `${Number(nextState.zrlBalance).toFixed(2)} ZRL`;
        }
        if (byId('asset-legacy-zrl')) byId('asset-legacy-zrl').textContent = Number(nextState.legacyPendingZrl || 0).toFixed(2);
        if (byId('asset-legacy-sol')) byId('asset-legacy-sol').textContent = Number(nextState.legacyPendingSol || 0).toFixed(4);
        const referralCode = String(nextState.referralCode || '');
        if (byId('affiliate-code')) byId('affiliate-code').textContent = referralCode || 'Unavailable';
        if (byId('affiliate-link')) {
            byId('affiliate-link').value = referralCode
                ? `https://t.me/Zer0lifelabs_ai_bot?start=ref_${encodeURIComponent(referralCode)}`
                : '';
        }
        if (byId('affiliate-levels')) {
            const levels = Array.isArray(nextState.affiliateLevels) ? nextState.affiliateLevels : [];
            byId('affiliate-levels').replaceChildren();
            levels.forEach((level) => {
                const row = document.createElement('div');
                row.style.cssText = 'display:flex;justify-content:space-between;gap:8px;padding:7px 9px;border-radius:10px;background:rgba(255,255,255,.035);font-size:10px;';
                const label = document.createElement('span');
                label.style.color = '#9ca39a';
                label.textContent = `Level ${Number(level.level)} · ${Number(level.ratePercent)}% · ${Number(level.users)} runners`;
                const total = document.createElement('strong');
                total.style.color = '#d8ff62';
                total.textContent = `${Number(level.earnedZrl || 0).toLocaleString()} ZRL`;
                row.append(label, total);
                byId('affiliate-levels').appendChild(row);
            });
        }
        if (typeof totalBalance !== 'undefined') {
            totalBalance = Number(nextState.availableZrl || 0);
            earnedZrlBalance = totalBalance;
            unclaimedZRL = Number(nextState.pendingRunZrl || 0);
            poolDeposit = Number(nextState.gameSolBalance || 0);
            casinoTotalWon = 0;
            totalCasinoTurnover = 0;
            freeSpins = Number(nextState.freeSpins || 0);
            if (typeof currentDay !== 'undefined') currentDay = Number(nextState.checkinDay || 1);
            if (typeof lastCheckinDate !== 'undefined') lastCheckinDate = nextState.lastCheckinDate || '';
            if (typeof ZRL_PER_KM !== 'undefined') ZRL_PER_KM = Number(nextState.boosterRate || 300);
            if (typeof activeFarms !== 'undefined' && Array.isArray(nextState.farms)) {
                activeFarms = nextState.farms.map((farm) => ({
                    id: farm.id,
                    asset: farm.asset,
                    amount: farm.amount,
                    months: Number(farm.months || 1),
                    dailyRate: farm.dailyRate,
                    startTime: farm.startTime,
                    endTime: farm.endTime,
                    claimedReward: Number(farm.claimedReward || 0),
                }));
            }
            if (typeof updateUI === 'function') updateUI();
            if (typeof updateEnergyUI === 'function') updateEnergyUI();
            if (typeof updateCheckinUI === 'function') updateCheckinUI();
        }
        ['withdraw-zrl-btn', 'withdraw-sol-btn'].forEach((id) => {
            const button = byId(id);
            if (button) button.disabled = !address;
        });
    }

    async function copyReferralLink() {
        const input = byId('affiliate-link');
        if (!input?.value) {
            alertUser('Your invite code is still loading. Try again in a moment.');
            return;
        }
        try {
            await navigator.clipboard.writeText(input.value);
            alertUser('Invite link copied. Share it with a new runner.');
        } catch {
            input.focus();
            input.select();
            window.prompt('Copy your Zer0Life Run invite link:', input.value);
        }
    }

    async function refresh() {
        try {
            const legacy = preserveLegacyBalance();
            await post('/wallet/legacy', { amount: legacy.zrl, solAmount: legacy.sol });
            const nextState = await post('/wallet/state');
            renderWallet(nextState);
            return nextState;
        } catch (error) {
            const label = byId('phantom-link-label');
            if (label) label.textContent = 'Wallet service unavailable';
            console.warn('Zer0Life wallet state:', error.message);
            throw error;
        }
    }

    async function signMessage(message, connectedAddress) {
        const wallet = provider();
        if (!wallet?.signMessage) {
            throw new Error('Open this app in Phantom’s browser to sign wallet messages.');
        }
        const result = await wallet.signMessage(new TextEncoder().encode(message), 'utf8');
        const activeAddress = wallet.publicKey?.toString();
        if (!result?.signature || activeAddress !== connectedAddress) {
            throw new Error('Phantom signature did not match the linked wallet.');
        }
        return toBase64(result.signature);
    }

    async function connect() {
        if (busy) return;
        busy = true;
        try {
            const wallet = provider();
            if (!wallet) {
                throw new Error('Phantom was not detected. Open Zer0Life Run in Phantom’s in-app browser, then connect again.');
            }
            const connection = await wallet.connect();
            const address = connection.publicKey?.toString() || wallet.publicKey?.toString();
            if (!address) throw new Error('Phantom did not return a wallet address.');
            const challenge = await post('/wallet/challenge', { purpose: 'link', address });
            const signature = await signMessage(challenge.message, address);
            await post('/wallet/link', { address, nonce: challenge.nonce, signature });
            alertUser('Phantom wallet linked. Your balances are now verified.');
            await refresh();
        } catch (error) {
            alertUser(error.message || 'Could not connect Phantom.');
        } finally {
            busy = false;
        }
    }

    function askAmount(asset, maximum) {
        return new Promise((resolve) => {
            const promptFn = window.showCustomPrompt;
            if (typeof promptFn === 'function') {
                promptFn(
                    `Withdraw ${asset}`,
                    `Available: ${Number(maximum).toFixed(asset === 'SOL' ? 4 : 2)} ${asset}`,
                    `Enter ${asset} amount…`,
                    resolve
                );
            } else {
                resolve(window.prompt(`Amount to withdraw (available ${maximum} ${asset})`));
            }
        });
    }

    async function withdraw(asset) {
        if (busy) return;
        if (!state?.walletAddress) {
            alertUser('Connect and verify Phantom before withdrawing.');
            return;
        }
        const maximum = asset === 'SOL' ? Number(state.gameSolBalance || 0) : Number(state.availableZrl || 0);
        if (maximum <= 0) {
            alertUser(`No verified ${asset} balance is available to withdraw.`);
            return;
        }
        const amountText = await askAmount(asset, maximum);
        if (amountText == null) return;
        const amount = Number(amountText);
        if (!Number.isFinite(amount) || amount <= 0 || amount > maximum) {
            alertUser(`Enter a valid ${asset} amount up to ${maximum}.`);
            return;
        }
        busy = true;
        try {
            const purpose = asset === 'SOL' ? 'withdraw_sol' : 'withdraw_zrl';
            const challenge = await post('/wallet/challenge', {
                purpose, address: state.walletAddress, amount,
            });
            const signature = await signMessage(challenge.message, state.walletAddress);
            const result = await post('/wallet/withdraw', {
                nonce: challenge.nonce, signature,
            });
            alertUser(`${amount} ${asset} transfer submitted.\nSolana signature: ${result.txHash}`);
            await refresh();
        } catch (error) {
            alertUser(error.message || 'Withdrawal could not be processed.');
            await refresh().catch(() => {});
        } finally {
            busy = false;
        }
    }

    async function playGame(game, payload = {}) {
        const eventId = window.crypto?.randomUUID
            ? window.crypto.randomUUID()
            : `zr-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const result = await post('/wallet/games/reward', { game, eventId, ...payload });
        renderWallet(Object.assign({}, state || {}, result));
        return result;
    }

    async function spend(purpose, payload = {}) {
        const eventId = window.crypto?.randomUUID
            ? window.crypto.randomUUID()
            : `zr-spend-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const result = await post('/wallet/spend', { purpose, eventId, ...payload });
        renderWallet(Object.assign({}, state || {}, result));
        return result;
    }

    window.secureWallet = {
        request: post,
        refresh,
        connect,
        withdraw,
        playGame,
        spend,
        applyResult: (result) => renderWallet(Object.assign({}, state || {}, result)),
        getState: () => state,
        setState: renderWallet,
    };

    if (typeof totalBalance !== 'undefined') {
        totalBalance = 0;
        earnedZrlBalance = 0;
        unclaimedZRL = 0;
        poolDeposit = 0;
        solBalance = 0;
        casinoTotalWon = 0;
        totalCasinoTurnover = 0;
        freeSpins = 0;
    }
    byId('connect-phantom-btn')?.addEventListener('click', connect);
    byId('affiliate-copy-btn')?.addEventListener('click', copyReferralLink);
    ['wallet-btn', 'nav-wallet'].forEach((id) => {
        byId(id)?.addEventListener('click', () => { setTimeout(() => refresh().catch(() => {}), 0); });
    });
    refresh().catch(() => {});
})();
