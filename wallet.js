(() => {
    const API_BASE = 'https://zer0life-ai-iz5n.onrender.com';
    const telegramApp = window.Telegram?.WebApp;
    const initData = () => telegramApp?.initData || '';
    const byId = (id) => document.getElementById(id);
    let state = null;
    let busy = false;
    let sdkFailed = false;

    async function post(path, payload = {}) {
        const response = await fetch(`${API_BASE}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            cache: 'no-store',
            body: JSON.stringify({ initData: initData(), ...payload }),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success) {
            const error = new Error(result.error || `Wallet request failed (${response.status})`);
            error.status = response.status;
            throw error;
        }
        return result;
    }

    function alertUser(message) {
        let text = String(message || 'Не удалось выполнить действие.');
        if (text.length > 256) {
            text = text.slice(0, 255);
            if (/[\uD800-\uDBFF]$/.test(text)) text = text.slice(0, -1);
            text += '…';
        }
        if (telegramApp?.showAlert) telegramApp.showAlert(text);
        else window.alert(message);
    }

    function toBase64(bytes) {
        let binary = '';
        new Uint8Array(bytes).forEach((byte) => { binary += String.fromCharCode(byte); });
        return btoa(binary);
    }

    function embeddedProvider() {
        if (!window.embeddedWallet) {
            throw new Error('Встроенный кошелёк ещё не загружен. Откройте приложение заново в Telegram.');
        }
        return window.embeddedWallet;
    }

    function renderEmbeddedStatus() {
        const label = byId('embedded-wallet-label');
        const addressLabel = byId('embedded-wallet-address');
        const status = byId('embedded-wallet-status');
        const button = byId('setup-embedded-wallet-btn');
        if (!button) return;
        const sdk = window.embeddedWallet?.getStatus();
        const sameUser = Boolean(state?.telegramId && sdk?.ready
            && String(sdk.telegramId) === String(state.telegramId));
        const linked = Boolean(sameUser && state.walletAddress && sdk.address === state.walletAddress);
        let message = 'Вход через Telegram… Создание кошелька не изменяет игровой баланс.';
        let title = 'Встроенный Solana-кошелёк';
        let action = sdk?.address ? 'Активировать мой кошелёк' : 'Создать личный кошелёк';
        let disabled = !sameUser || busy;
        if (!initData()) {
            message = 'Откройте Zer0Life Run через @Zer0lifelabs_ai_bot в Telegram.';
            action = 'Доступно внутри Telegram';
            disabled = true;
        } else if (sdkFailed) {
            message = 'Модуль кошелька не загрузился или вход не завершён. Игровые балансы сохранены.';
            action = 'Повторить загрузку кошелька';
            disabled = busy;
        } else if (!state?.telegramId) {
            message = state ? 'Сервер ещё не обновлён для встроенного кошелька. Создание и вывод временно заблокированы.'
                : 'Загружаем ваш подтверждённый профиль и баланс…';
        } else if (sdk?.authenticated && String(sdk.telegramId) !== String(state.telegramId)) {
            message = 'В кошельке осталась другая сессия. Сбросьте только вход кошелька; баланс игры не меняется.';
            action = 'Сбросить вход кошелька';
            disabled = busy;
        } else if (linked) {
            title = 'Ваш личный Solana-кошелёк';
            message = 'Адрес подтверждён сервером. Подпись для вывода выполняется внутри приложения.';
        } else if (sameUser && state.walletAddress && sdk.address !== state.walletAddress) {
            title = 'Сохранён ранее привязанный адрес';
            message = 'К профилю уже привязан другой кошелёк. Автоматическая замена отключена; балансы сохранены.';
            action = 'Требуется безопасная смена адреса';
            disabled = true;
        } else if (sameUser) {
            message = 'Создайте или активируйте свой кошелёк прямо здесь — без перехода в браузер.';
        }
        label.textContent = title;
        addressLabel.textContent = state?.walletAddress || (sameUser ? sdk.address : '')
            || 'Личный адрес для вашего Telegram-аккаунта';
        status.textContent = message;
        button.textContent = busy ? 'Подтверждение…' : action;
        button.disabled = disabled;
        button.hidden = linked;
        if (byId('embedded-wallet-tools')) byId('embedded-wallet-tools').hidden = !linked;
        ['withdraw-zrl-btn', 'withdraw-sol-btn', 'wallet-export-btn'].forEach(id => {
            if (byId(id)) byId(id).disabled = !linked || busy;
        });
    }

    function preserveLegacyBalance() {
        if (typeof STORAGE_PREFIX === 'undefined') return { zrl: 0, sol: 0 };
        if (window.legacyRecovery) return window.legacyRecovery.preserve(STORAGE_PREFIX);
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
        window.legacyRecovery?.setState(nextState);
        window.recoveryActions?.setState(nextState);
        window.legacyFarmReview?.setState(nextState);
        const address = nextState.walletAddress || '';
        renderEmbeddedStatus();
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
                if (typeof verifiedFarmsLoaded !== 'undefined') verifiedFarmsLoaded = true;
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
        if (byId('farming-modal')?.style.display === 'flex' && typeof window.renderFarmingDeposits === 'function') {
            window.renderFarmingDeposits();
        }
        const pendingList = byId('pending-withdrawals');
        if (pendingList) {
            pendingList.replaceChildren();
            (nextState.pendingWithdrawals || []).forEach(withdrawal => {
                const button = document.createElement('button');
                button.className = 'wallet-action-btn';
                button.style.cssText = 'width:100%;margin:6px 0;font-size:11px;';
                button.textContent = `${withdrawal.asset}: проверить ожидающий вывод`;
                button.addEventListener('click', () => checkWithdrawal(withdrawal.nonce).catch(error => alertUser(error.message)));
                pendingList.appendChild(button);
            });
        }
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
            try {
                const legacy = preserveLegacyBalance();
                await post('/wallet/legacy', { amount: legacy.zrl, solAmount: legacy.sol });
                if (window.recoveryActions) await window.recoveryActions.submit();
                window.legacyRecovery?.setWarning('');
            } catch {
                // A failed historical upload must not hide current server funds.
                window.legacyRecovery?.setWarning('Старая история пока не синхронизирована с сервером. Локальная копия не удалена; текущий баланс загружен отдельно.');
            }
            const nextState = await post('/wallet/state');
            renderWallet(nextState);
            return nextState;
        } catch (error) {
            const label = byId('embedded-wallet-label');
            if (label) label.textContent = 'Wallet service unavailable';
            console.warn('Zer0Life wallet state:', error.message);
            throw error;
        }
    }

    async function signMessage(message, connectedAddress, telegramId, title) {
        return toBase64(await embeddedProvider().signMessage({
            message, address: connectedAddress, telegramId, title,
        }));
    }

    async function connect() {
        if (busy) return;
        busy = true;
        renderEmbeddedStatus();
        try {
            if (sdkFailed) {
                window.location.reload();
                return;
            }
            const current = await refresh();
            const wallet = embeddedProvider();
            const sdk = wallet.getStatus();
            if (sdk.authenticated && String(sdk.telegramId) !== String(current.telegramId)) {
                await wallet.resetSession();
                window.location.reload();
                return;
            }
            const address = await wallet.connect({
                telegramId: current.telegramId, expectedAddress: current.walletAddress || '',
            });
            const challenge = await post('/wallet/challenge', { purpose: 'link', address });
            const signature = await signMessage(challenge.message, address, current.telegramId, 'Активировать личный кошелёк');
            await post('/wallet/link', { address, nonce: challenge.nonce, signature });
            await refresh();
            alertUser('Личный Solana-кошелёк подключён. Игровой баланс не изменён.');
        } catch (error) {
            alertUser(error.message || 'Не удалось активировать встроенный кошелёк.');
        } finally {
            busy = false;
            renderEmbeddedStatus();
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
        busy = true;
        renderEmbeddedStatus();
        try {
            if (!['SOL', 'ZRL'].includes(asset)) throw new Error('Неизвестный актив.');
            const current = await refresh();
            if (!current.walletAddress) throw new Error('Сначала создайте и подтвердите личный Solana-кошелёк.');
            const address = await embeddedProvider().connect({
                telegramId: current.telegramId, expectedAddress: current.walletAddress,
            });
            const maximum = asset === 'SOL' ? Number(current.gameSolBalance || 0) : Number(current.availableZrl || 0);
            if (maximum <= 0) throw new Error(`Нет доступного баланса ${asset} для вывода.`);
            const amountText = await askAmount(asset, maximum);
            if (amountText == null) return;
            const amount = Number(amountText);
            if (!Number.isFinite(amount) || amount <= 0 || amount > maximum) {
                throw new Error(`Укажите сумму ${asset} больше нуля и не больше ${maximum}.`);
            }
            const purpose = asset === 'SOL' ? 'withdraw_sol' : 'withdraw_zrl';
            const challenge = await post('/wallet/challenge', {
                purpose, address, amount,
            });
            const signature = await signMessage(challenge.message, address, current.telegramId, `Подтвердить вывод ${amount} ${asset}`);
            const result = await post('/wallet/withdraw', {
                nonce: challenge.nonce, signature,
            });
            alertUser(result.status === 'processing'
                ? `Перевод проверяется сетью. Средства зарезервированы.\nSolana: ${result.txHash}`
                : `${amount} ${asset} transfer submitted.\nSolana signature: ${result.txHash}`);
            await refresh();
        } catch (error) {
            alertUser(error.message || 'Withdrawal could not be processed.');
            await refresh().catch(() => {});
        } finally {
            busy = false;
            renderEmbeddedStatus();
        }
    }

    async function checkWithdrawal(nonce) {
        const result = await post('/wallet/withdrawals/status', { nonce });
        if (byId('wallet-withdrawal-status')) {
            byId('wallet-withdrawal-status').textContent = result.status === 'confirmed'
                ? 'Вывод подтверждён сетью Solana.'
                : result.status === 'failed' ? 'Сеть отклонила перевод; сумма возвращена на баланс.'
                    : 'Ожидаем подтверждения сети. Сумма остаётся зарезервирована.';
        }
        await refresh();
    }

    async function exportEmbeddedWallet() {
        if (busy) return;
        busy = true;
        renderEmbeddedStatus();
        try {
            const warning = 'Секретный ключ даёт полный доступ к кошельку. Не отправляйте его никому, включая поддержку. Открыть резервную копию?';
            const approved = telegramApp?.showConfirm
                ? await new Promise(resolve => telegramApp.showConfirm(warning, resolve))
                : window.confirm(warning);
            if (!approved) return;
            await embeddedProvider().exportWallet({
                address: state?.walletAddress, telegramId: state?.telegramId,
            });
        } catch (error) {
            alertUser(error.message);
        } finally {
            busy = false;
            renderEmbeddedStatus();
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
        checkWithdrawal,
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
    byId('setup-embedded-wallet-btn')?.addEventListener('click', connect);
    byId('wallet-export-btn')?.addEventListener('click', exportEmbeddedWallet);
    byId('wallet-copy-address-btn')?.addEventListener('click', async () => {
        try {
            if (!state?.walletAddress) throw new Error('Личный адрес ещё не подтверждён.');
            await navigator.clipboard.writeText(state.walletAddress);
            alertUser('Личный Solana-адрес скопирован.');
        } catch (error) {
            alertUser(error.message || 'Не удалось скопировать адрес.');
        }
    });
    window.addEventListener('zerolife:embedded-wallet', () => {
        if (window.embeddedWallet?.getStatus().ready) sdkFailed = false;
        renderEmbeddedStatus();
    });
    window.addEventListener('zerolife:wallet-sdk-error', () => {
        sdkFailed = true;
        renderEmbeddedStatus();
    });
    setTimeout(() => {
        if (initData() && !window.embeddedWallet?.getStatus().ready) {
            sdkFailed = true;
            renderEmbeddedStatus();
        }
    }, 35000);
    byId('affiliate-copy-btn')?.addEventListener('click', copyReferralLink);
    ['wallet-btn', 'nav-wallet'].forEach((id) => {
        byId(id)?.addEventListener('click', () => { setTimeout(() => refresh().catch(() => {}), 0); });
    });
    refresh().catch(() => {});
})();
