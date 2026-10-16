(() => {
    'use strict';
    let polling = false, timer = null;
    const key = () => typeof STORAGE_PREFIX === 'undefined' ? null : STORAGE_PREFIX + 'phantom_mobile_pending';
    const status = message => {
        const node = document.getElementById('wallet-withdrawal-status');
        if (node) node.textContent = message;
    };
    const alertUser = message => window.Telegram?.WebApp?.showAlert
        ? window.Telegram.WebApp.showAlert(message) : window.alert(message);
    const api = (path, body) => window.secureWallet.request(path, body);

    async function start(purpose, nonce) {
        if (!key()) throw new Error('Откройте приложение из Telegram.');
        const result = await api('/wallet/mobile/start', { purpose, nonce });
        localStorage.setItem(key(), JSON.stringify({
            id: result.sessionId, purpose, expiresAt: result.expiresAt,
        }));
        status('Подтвердите подключение и подпись в Phantom, затем вернитесь в Telegram.');
        if (window.Telegram?.WebApp?.openLink) window.Telegram.WebApp.openLink(result.url);
        else window.open(result.url, '_blank', 'noopener,noreferrer');
        schedule();
    }
    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(resume, 2500);
    }
    async function resume() {
        if (polling || !key() || !window.secureWallet) return;
        const saved = localStorage.getItem(key());
        if (!saved) return;
        polling = true;
        try {
            const pending = JSON.parse(saved);
            if (pending.expiresAt * 1000 < Date.now()) {
                localStorage.removeItem(key());
                status('Подтверждение Phantom истекло. Можно начать подключение или вывод заново.');
                return;
            }
            const result = await api('/wallet/mobile/status', { sessionId: pending.id });
            if (result.status === 'failed') {
                localStorage.removeItem(key());
                status(result.error || 'Подтверждение Phantom отменено.');
                return;
            }
            if (result.status !== 'complete') {
                schedule();
                return;
            }
            if (result.purpose !== pending.purpose) throw new Error('Назначение подписи Phantom не совпало.');
            const path = result.purpose === 'link' ? '/wallet/link' : '/wallet/withdraw';
            const completion = await api(path, { nonce: result.nonce, signature: result.signature, address: result.address });
            localStorage.removeItem(key());
            status(result.purpose === 'link' ? 'Phantom подключён. Подпись проверена сервером.'
                : completion.status === 'processing'
                    ? 'Транзакция проверяется в сети. Средства зарезервированы; повторный платёж не отправляется.'
                    : `Вывод отправлен в Solana: ${completion.txHash}`);
            await window.secureWallet.refresh();
            if (result.purpose === 'link') alertUser('Phantom подключён к Zer0Life Run. Можно выводить ZRL и SOL.');
        } catch (error) {
            if (error.status >= 400 && error.status < 500) {
                localStorage.removeItem(key());
                status(error.message);
                await window.secureWallet.refresh().catch(() => {});
            } else {
                status('Ответ сервера пока недоступен. Подтверждение сохранено; повторная проверка не создаёт второй платёж.');
                schedule();
            }
        } finally {
            polling = false;
        }
    }
    async function checkWithdrawal(nonce) {
        const result = await api('/wallet/withdrawals/status', { nonce });
        status(result.status === 'confirmed' ? 'Вывод подтверждён сетью Solana.'
            : result.status === 'failed' ? 'Сеть отклонила перевод; сумма возвращена на баланс.'
                : 'Ожидаем подтверждения сети. Сумма остаётся зарезервирована.');
        await window.secureWallet.refresh();
    }
    window.phantomMobile = { start, resume, checkWithdrawal };
    window.addEventListener('pageshow', resume);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) resume(); });
})();
