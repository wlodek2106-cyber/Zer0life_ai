(() => {
    const API_BASE = 'https://zer0life-ai-iz5n.onrender.com';
    const telegramApp = window.Telegram?.WebApp;
    const initData = () => telegramApp?.initData || '';
    const byId = (id) => document.getElementById(id);
    let state = null;
    let busy = false;
    let merchantTask = null;
    let releaseGateShown = false;
    let withdrawalStatusTimer = null;
    let withdrawalStatusCheckBusy = false;

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
        let text = String(message || 'Could not complete the action.');
        if (text.length > 256) {
            text = text.slice(0, 255);
            if (/[\uD800-\uDBFF]$/.test(text)) text = text.slice(0, -1);
            text += '…';
        }
        if (telegramApp?.showAlert) telegramApp.showAlert(text);
        else window.alert(message);
    }

    function renderWithdrawalControls() {
        const ready = Boolean(initData() && state?.telegramId
            && ['telegram_otp_v1', 'trusted_destination_v2'].includes(state?.withdrawalMode));
        ['withdraw-zrl-btn', 'withdraw-sol-btn'].forEach(id => {
            if (byId(id)) byId(id).disabled = busy || !ready;
        });
        const status = byId('wallet-withdrawal-status');
        if (state && !ready && status) {
            releaseGateShown = true;
            status.textContent = initData()
                ? 'Waiting for the server update required for Telegram withdrawals. Your balances are safe.'
                : 'Withdrawals are available inside Telegram via @Zer0lifelabs_ai_bot.';
        } else if (ready && status && releaseGateShown) {
            status.textContent = '';
            releaseGateShown = false;
        }
    }

    function assertSession(owner) {
        if (!owner || initData() !== owner) throw new Error('Your Telegram profile changed. Reopen Wallet. No funds were sent.');
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
        renderWithdrawalControls();
        const trustedDestination = byId('wallet-trusted-destination');
        if (trustedDestination) {
            const trustedAddress = String(nextState.trustedWithdrawalAddress || '');
            trustedDestination.textContent = trustedAddress
                ? `Saved withdrawal address (no repeat code): ${trustedAddress.slice(0, 7)}…${trustedAddress.slice(-6)}. A new address must be verified with a Telegram code.`
                : 'Your first withdrawal address will be verified with a Telegram code. You will not need another code for that address.';
        }
        const pendingTransfers = nextState.pendingWithdrawals || [];
        const withdrawalStatus = byId('wallet-withdrawal-status');
        if (withdrawalStatus && pendingTransfers.length
                && ['telegram_otp_v1', 'trusted_destination_v2'].includes(nextState.withdrawalMode)) {
            withdrawalStatus.textContent = `${pendingTransfers.length} ${pendingTransfers.length === 1 ? 'transfer has' : 'transfers have'} been submitted and ${pendingTransfers.length === 1 ? 'is' : 'are'} awaiting Solana confirmation. Status updates automatically; do not submit again.`;
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
            if (typeof boosterExpiryTime !== 'undefined') boosterExpiryTime = Number(nextState.boosterExpires || 0);
            if (typeof activeBoosterName !== 'undefined') {
                const rate = Number(nextState.boosterRate || 300);
                const names = { 300: 'Standard', 1500: 'Boots x2', 3000: 'Starter',
                    6000: 'Standard', 13000: 'Pro Tier', 30000: 'Whale' };
                activeBoosterName = `${names[rate] || 'Booster'} (${rate.toLocaleString()} ZRL/km)`;
            }
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
            (nextState.pendingWithdrawalApprovals || []).forEach(approval => {
                const row = document.createElement('div');
                const label = document.createElement('p');
                label.style.cssText = 'font-size:11px;overflow-wrap:anywhere;';
                label.textContent = `${approval.amount} ${approval.asset} → ${approval.address}. Waiting for your Telegram code; funds have not been deducted.`;
                row.appendChild(label);
                for (const cancel of [false, true]) {
                    const button = document.createElement('button');
                    button.className = 'wallet-action-btn';
                    button.textContent = cancel ? 'Cancel request' : 'Enter Telegram code';
                    button.addEventListener('click', () => approveWithdrawal(approval.nonce, cancel));
                    row.appendChild(button);
                }
                pendingList.appendChild(row);
            });
            pendingTransfers.forEach(withdrawal => {
                const row = document.createElement('div');
                const label = document.createElement('p');
                label.style.cssText = 'font-size:11px;line-height:1.45;overflow-wrap:anywhere;margin:8px 0;';
                const amount = Number(withdrawal.amount || 0).toFixed(withdrawal.asset === 'SOL' ? 4 : 2);
                const destination = String(withdrawal.address || '');
                const shortDestination = destination ? `${destination.slice(0, 7)}…${destination.slice(-6)}` : 'address hidden';
                const progress = withdrawal.status === 'processing'
                    ? 'processing'
                    : 'awaiting final Solana confirmation';
                label.textContent = `${withdrawal.asset} ${amount} → ${shortDestination} · ${progress}${withdrawal.txHash ? ` · ${withdrawal.txHash.slice(0, 8)}…${withdrawal.txHash.slice(-6)}` : ''}`;
                row.appendChild(label);
                pendingList.appendChild(row);
            });
        }
        scheduleWithdrawalStatusChecks();
        if (!merchantTask && nextState.lastMerchantPurchase) {
            const receipt = nextState.lastMerchantPurchase;
            merchantMessage(receipt.status === 'confirmed'
                ? 'Your latest Merchant payment was confirmed on Solana.'
                : receipt.status === 'failed' ? 'Your latest payment failed. The cost was refunded.'
                    : 'Your Merchant payment is awaiting confirmation.', receipt);
        }
        if (!merchantTask && nextState.merchantPaymentsEnabled && Array.isArray(nextState.pendingPurchases)) {
            try {
                const saved = JSON.parse(localStorage.getItem(purchaseStorageKey()) || 'null');
                const purchaseId = nextState.pendingPurchases[0]?.purchaseId || saved?.eventId;
                if (purchaseId) {
                    merchantTask = monitorPurchase(purchaseId)
                        .catch(error => merchantMessage(error.message))
                        .finally(() => { merchantTask = null; });
                }
            } catch (error) {
                merchantMessage(error.message);
            }
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
                window.legacyRecovery?.setWarning('Your legacy history has not synced with the server yet. The local copy is preserved; your current balance was loaded separately.');
            }
            const nextState = await post('/wallet/state');
            renderWallet(nextState);
            return nextState;
        } catch (error) {
            state = null;
            renderWithdrawalControls();
            const label = byId('wallet-withdrawal-status');
            if (label) label.textContent = 'Could not refresh Wallet. Balances were not changed. Reopen Wallet.';
            console.warn('Zer0Life wallet state:', error.message);
            throw error;
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
        const owner = initData();
        busy = true;
        renderWithdrawalControls();
        try {
            if (!['SOL', 'ZRL'].includes(asset)) throw new Error('Unknown asset.');
            const current = await refresh();
            assertSession(owner);
            if (!current.telegramId
                    || !['telegram_otp_v1', 'trusted_destination_v2'].includes(current.withdrawalMode)) {
                throw new Error('The server is still updating. Withdrawals will be available after the update; no funds were deducted.');
            }
            const pending = current.pendingWithdrawalApprovals || [];
            if (pending.length) {
                if (pending[0].asset !== asset) throw new Error('Approve or cancel your previous withdrawal request in Wallet first.');
                await submitApproval(pending[0]);
                return;
            }
            if ((current.pendingWithdrawals || []).some(row => row.asset === asset)) {
                throw new Error('Your previous withdrawal is still awaiting Solana confirmation. No action is needed; try again after it completes.');
            }
            const maximum = asset === 'SOL' ? Number(current.gameSolBalance || 0) : Number(current.availableZrl || 0);
            if (maximum <= 0) throw new Error(`No available ${asset} balance to withdraw.`);
            const amountText = await askAmount(asset, maximum);
            assertSession(owner);
            if (amountText == null) return;
            const amount = Number(amountText);
            if (!Number.isFinite(amount) || amount <= 0 || amount > maximum) {
                throw new Error(`Enter an amount greater than zero and no more than ${maximum} ${asset}.`);
            }
            const rawAddress = await askText('Recipient address', `Withdraw ${amount} ${asset} from the game. Enter the recipient's Solana wallet address, not a token account address.`, 'Solana wallet address…');
            assertSession(owner);
            if (rawAddress == null) return;
            const address = String(rawAddress).trim();
            if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(address)) throw new Error('Invalid Solana address.');
            const approved = telegramApp?.showConfirm
                ? await new Promise(resolve => telegramApp.showConfirm(
                    `Withdraw ${amount} ${asset} to:\n${address}\n${
                        address === current.trustedWithdrawalAddress
                            ? 'This address is already verified; you will not need another code.'
                            : 'A new address requires a Telegram code. After verification, it will be saved as trusted.'
                    }`, resolve))
                : window.confirm(`Withdraw ${amount} ${asset} to ${address}? ${
                    address === current.trustedWithdrawalAddress
                        ? 'This address is verified.'
                        : 'A Telegram code will verify and save the new address.'
                }`);
            assertSession(owner);
            if (!approved) return;
            const approval = await post('/wallet/withdrawals/request', {
                asset, address, amount: String(amountText).trim(), requestId: window.crypto.randomUUID(),
            });
            assertSession(owner);
            if (approval.status) {
                alertUser(approval.status === 'confirmed'
                    ? 'Withdrawal confirmed on Solana.'
                    : `Withdrawal submitted to Solana. The amount is reserved; do not submit it again.${approval.txHash ? '\nSolana: ' + approval.txHash : ''}`);
                await refresh();
                return;
            }
            await refresh();
            await submitApproval(approval);
        } catch (error) {
            alertUser(error.message || 'Withdrawal could not be processed.');
            await refresh().catch(() => {});
        } finally {
            busy = false;
            renderWithdrawalControls();
        }
    }

    function askText(title, detail, placeholder) {
        return new Promise(resolve => {
            if (typeof window.showCustomPrompt === 'function') {
                window.showCustomPrompt(title, detail, placeholder, resolve);
            } else resolve(window.prompt(`${title}\n${detail}`));
        });
    }

    async function submitApproval(approval) {
        const owner = initData();
        const value = await askText(
            'Confirm withdrawal',
            `${approval.amount} ${approval.asset} → ${approval.address}\nA code was sent in your private chat with @Zer0lifelabs_ai_bot. It expires in 5 minutes. Cancelling does not deduct funds; you can resume this request in Wallet.`,
            '6-digit Telegram code…',
        );
        assertSession(owner);
        if (value == null) return;
        const code = String(value).trim();
        if (!/^\d{6}$/.test(code)) throw new Error('Enter the 6-digit code from the bot message.');
        const result = await post('/wallet/withdrawals/confirm', { nonce: approval.nonce, code });
        alertUser(result.status === 'confirmed' ? 'Withdrawal confirmed on Solana.'
            : `Your transfer is processing. The amount is reserved; do not submit another withdrawal.${result.txHash ? '\nSolana: ' + result.txHash : ''}`);
        await refresh();
    }

    async function approveWithdrawal(nonce, cancel = false) {
        if (busy) return;
        busy = true;
        renderWithdrawalControls();
        try {
            const current = await refresh();
            const item = (current.pendingWithdrawalApprovals || []).find(row => row.nonce === nonce);
            if (!item) throw new Error('This request expired or was already processed. Refresh Wallet.');
            if (cancel) await post('/wallet/withdrawals/cancel', { nonce });
            else await submitApproval(item);
            await refresh();
        } catch (error) {
            alertUser(error.message);
            await refresh().catch(() => {});
        } finally {
            busy = false;
            renderWithdrawalControls();
        }
    }

    function scheduleWithdrawalStatusChecks() {
        const pending = state?.pendingWithdrawals || [];
        const ready = Boolean(initData() && state?.telegramId
            && ['telegram_otp_v1', 'trusted_destination_v2'].includes(state?.withdrawalMode));
        if (!pending.length || !ready) {
            if (withdrawalStatusTimer !== null) clearTimeout(withdrawalStatusTimer);
            withdrawalStatusTimer = null;
            return;
        }
        if (withdrawalStatusTimer !== null || withdrawalStatusCheckBusy) return;
        withdrawalStatusTimer = setTimeout(async () => {
            withdrawalStatusTimer = null;
            if (withdrawalStatusCheckBusy) return;
            withdrawalStatusCheckBusy = true;
            const owner = initData();
            try {
                const snapshot = (state?.pendingWithdrawals || []).slice();
                for (const withdrawal of snapshot) {
                    if (initData() !== owner) break;
                    if (!(state?.pendingWithdrawals || []).some(item => item.nonce === withdrawal.nonce)) continue;
                    await checkWithdrawal(withdrawal.nonce, false);
                }
                if (initData() === owner) await refresh();
            } catch {
                const status = byId('wallet-withdrawal-status');
                if (status && (state?.pendingWithdrawals || []).length) {
                    status.textContent = 'Could not refresh the status. We will retry automatically; the transfer will not be submitted again.';
                }
            } finally {
                withdrawalStatusCheckBusy = false;
                scheduleWithdrawalStatusChecks();
            }
        }, 15000);
    }

    async function checkWithdrawal(nonce, refreshAfter = true) {
        const result = await post('/wallet/withdrawals/status', { nonce });
        if (byId('wallet-withdrawal-status')) {
            const amount = Number(result.amount || 0).toFixed(result.asset === 'SOL' ? 4 : 2);
            byId('wallet-withdrawal-status').textContent = result.status === 'confirmed'
                ? `${result.asset} ${amount}: confirmed on Solana.`
                : result.status === 'failed' ? `${result.asset} ${amount}: rejected by the network; funds were returned to your balance.`
                    : `${result.asset} ${amount}: still awaiting Solana confirmation. Do not submit it again.`;
        }
        if (refreshAfter) await refresh();
        return result;
    }

    async function playGame(game, payload = {}) {
        const eventId = window.crypto?.randomUUID
            ? window.crypto.randomUUID()
            : `zr-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const result = await post('/wallet/games/reward', { game, eventId, ...payload });
        renderWallet(Object.assign({}, state || {}, result));
        return result;
    }

    function merchantMessage(message, result) {
        const label = byId('booster-payment-status');
        if (label) label.textContent = message;
        const link = byId('booster-payment-tx');
        if (link && result?.txHash) {
            link.href = `https://explorer.solana.com/tx/${encodeURIComponent(result.txHash)}`;
            link.hidden = false;
        }
    }

    function purchaseStorageKey() {
        const user = telegramApp?.initDataUnsafe?.user?.id || state?.referralCode;
        if (!user) throw new Error('Wait for your profile to load before making a purchase.');
        return `zerolife-merchant-purchase:${user}`;
    }

    async function monitorPurchase(purchaseId, ownerKey = purchaseStorageKey()) {
        for (let attempt = 0; attempt < 20; attempt++) {
            if (purchaseStorageKey() !== ownerKey) throw new Error('Your profile changed. Open Wallet to check your purchase.');
            const result = await post('/wallet/purchases/status', { purchaseId });
            if (purchaseStorageKey() !== ownerKey) throw new Error('Your profile changed. Another player’s result was not applied.');
            renderWallet(Object.assign({}, state || {}, result));
            if (result.status === 'confirmed' || result.status === 'failed') {
                const key = purchaseStorageKey();
                const saved = JSON.parse(localStorage.getItem(key) || 'null');
                if (saved?.eventId === purchaseId) localStorage.removeItem(key);
                if (result.status === 'failed') {
                    merchantMessage(result.message || 'Payment failed. The cost was returned to your game balance.', result);
                    throw new Error(result.message || 'Payment failed. The cost was returned to your game balance.');
                }
                merchantMessage('Merchant payment confirmed on Solana. Boots activated.', result);
                return result;
            }
            merchantMessage('SOL is reserved. Waiting for Merchant payment confirmation; do not pay again.', result);
            await new Promise(resolve => setTimeout(resolve, 3000));
        }
        merchantMessage('The transfer is still being checked. Boots will activate after confirmation. Checking will resume the next time you open the app.');
        return { success: true, status: 'pending', purchaseId };
    }

    async function spend(purpose, payload = {}) {
        if (purpose === 'booster') {
            if (merchantTask) throw new Error('Your previous payment is still being checked. Another charge is blocked.');
            if (!state?.merchantPaymentsEnabled) {
                throw new Error('The Boots payment server has not been updated yet. You were not charged.');
            }
            const key = purchaseStorageKey();
            const saved = JSON.parse(localStorage.getItem(key) || 'null');
            if (saved && Number(saved.costSol) !== Number(payload.costSol)) {
                throw new Error('Check your previous Boots purchase first. Open Wallet to refresh its status.');
            }
            const eventId = saved?.eventId || (window.crypto?.randomUUID
                ? window.crypto.randomUUID()
                : `zr-boots-${Date.now()}-${Math.random().toString(36).slice(2)}`);
            // Persist BEFORE the request: a lost HTTP response must not cause a
            // new payment ID, including after closing Telegram or reloading.
            localStorage.setItem(key, JSON.stringify({ eventId, costSol: payload.costSol }));
            merchantTask = (async () => {
                merchantMessage('Preparing payment to Merchant…');
                if (byId('booster-payment-tx')) byId('booster-payment-tx').hidden = true;
                let result;
                try {
                    result = await post('/wallet/spend', { ...payload, purpose, eventId });
                } catch (error) {
                    // Definitive validation failures cannot have reserved SOL.
                    if ([400, 401, 402, 403, 409].includes(error.status)) localStorage.removeItem(key);
                    merchantMessage('Check the payment status in Wallet. Do not send another payment manually.');
                    throw error;
                }
                if (purchaseStorageKey() !== key) throw new Error('Your profile changed. Another player’s result was not applied.');
                renderWallet(Object.assign({}, state || {}, result));
                return monitorPurchase(result.purchaseId, key);
            })();
            try {
                return await merchantTask;
            } finally {
                merchantTask = null;
            }
        }
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
        withdraw,
        approveWithdrawal,
        checkWithdrawal,
        playGame,
        spend,
        checkPurchase: async (purchaseId) => {
            if (merchantTask) return merchantTask;
            merchantTask = monitorPurchase(purchaseId);
            try { return await merchantTask; } finally { merchantTask = null; }
        },
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
    byId('affiliate-copy-btn')?.addEventListener('click', copyReferralLink);
    ['wallet-btn', 'nav-wallet'].forEach((id) => {
        byId(id)?.addEventListener('click', () => { setTimeout(() => refresh().catch(() => {}), 0); });
    });
    refresh().catch(() => {});
})();
