(() => {
    'use strict';
    let polling = false, starting = false, timer = null;
    const key = () => typeof STORAGE_PREFIX === 'undefined' ? null : STORAGE_PREFIX + 'phantom_mobile_pending';
    const status = message => {
        const node = document.getElementById('wallet-withdrawal-status');
        if (node) node.textContent = message;
    };
    const alertUser = message => window.Telegram?.WebApp?.showAlert
        ? window.Telegram.WebApp.showAlert(message) : window.alert(message);
    const api = (path, body) => window.secureWallet.request(path, body);

    function handoff(pending) {
        const url = new URL(pending.url);
        if (!['https://phantom.com', 'https://phantom.app'].includes(url.origin)
            || url.username || url.password || url.hash || url.pathname !== '/ul/v1/connect') {
            throw new Error('Сервер вернул неподдерживаемую ссылку Phantom. Подключение не открыто.');
        }
        const panel = document.getElementById('phantom-mobile-handoff');
        const native = document.getElementById('phantom-native-open');
        const universal = document.getElementById('phantom-universal-open');
        if (!panel || !native || !universal) {
            throw new Error('Перезагрузите мини-приложение через меню Telegram, чтобы обновить подключение Phantom.');
        }
        // A real anchor tap preserves iOS user activation. Never auto-open a
        // browser after awaiting an API request, and never use Phantom Browse.
        native.href = 'phantom://' + url.pathname.slice('/ul/'.length) + url.search;
        universal.href = url.href;
        panel.hidden = false;
    }
    function clearHandoff() {
        const panel = document.getElementById('phantom-mobile-handoff');
        if (panel) panel.hidden = true;
        for (const id of ['phantom-native-open', 'phantom-universal-open']) {
            document.getElementById(id)?.removeAttribute('href');
        }
    }
    async function start(purpose, nonce) {
        if (!key()) throw new Error('Откройте приложение из Telegram.');
        if (starting) return;
        starting = true;
        try {
            const saved = localStorage.getItem(key());
            const pending = saved ? JSON.parse(saved) : null;
            if (pending?.expiresAt * 1000 > Date.now() && pending.url
                && pending.purpose === purpose && pending.nonce === nonce) {
                if (pending.stage === 'signing') {
                    status('Подключение уже одобрено. Завершите подпись в Phantom и вернитесь в Telegram.');
                    schedule();
                    return;
                }
                handoff(pending);
                status('Нажмите «Открыть приложение Phantom», затем подтвердите подключение и подпись.');
                schedule();
                return;
            }
            const result = await api('/wallet/mobile/start', { purpose, nonce });
            if (!result.sessionId || !Number.isFinite(result.expiresAt)
                || result.expiresAt * 1000 <= Date.now()) {
                throw new Error('Не удалось подготовить действующее подключение Phantom. Попробуйте ещё раз.');
            }
            const next = { id: result.sessionId, purpose, nonce, expiresAt: result.expiresAt, url: result.url };
            handoff(next);
            try {
                localStorage.setItem(key(), JSON.stringify(next));
            } catch {
                clearHandoff();
                throw new Error('Не удалось сохранить подключение на устройстве. Phantom не открыт; баланс не изменён.');
            }
            status('Подключение готово. Нажмите «Открыть приложение Phantom» ниже — автоматический переход в браузер отключён.');
            schedule();
        } finally {
            starting = false;
        }
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
                clearHandoff();
                status('Подтверждение Phantom истекло. Можно начать подключение или вывод заново.');
                return;
            }
            const result = await api('/wallet/mobile/status', { sessionId: pending.id });
            if (result.status === 'failed') {
                localStorage.removeItem(key());
                clearHandoff();
                status(result.error || 'Подтверждение Phantom отменено.');
                return;
            }
            if (result.status !== 'complete') {
                if (result.status === 'signing') {
                    localStorage.setItem(key(), JSON.stringify({ ...pending, stage: 'signing' }));
                    clearHandoff();
                    status('Phantom принял подключение. Завершите подпись в Phantom, затем вернитесь в Telegram.');
                } else if (pending.url) handoff(pending);
                schedule();
                return;
            }
            if (result.purpose !== pending.purpose) throw new Error('Назначение подписи Phantom не совпало.');
            const path = result.purpose === 'link' ? '/wallet/link' : '/wallet/withdraw';
            const completion = await api(path, { nonce: result.nonce, signature: result.signature, address: result.address });
            localStorage.removeItem(key());
            clearHandoff();
            status(result.purpose === 'link' ? 'Phantom подключён. Подпись проверена сервером.'
                : completion.status === 'processing'
                    ? 'Транзакция проверяется в сети. Средства зарезервированы; повторный платёж не отправляется.'
                    : `Вывод отправлен в Solana: ${completion.txHash}`);
            await window.secureWallet.refresh();
            if (result.purpose === 'link') alertUser('Phantom подключён к Zer0Life Run. Можно выводить ZRL и SOL.');
        } catch (error) {
            if (error.status >= 400 && error.status < 500) {
                localStorage.removeItem(key());
                clearHandoff();
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
