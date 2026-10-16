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
            throw new Error('The server returned an unsupported Phantom link. The connection was not opened.');
        }
        const panel = document.getElementById('phantom-mobile-handoff');
        const native = document.getElementById('phantom-native-open');
        const universal = document.getElementById('phantom-universal-open');
        if (!panel || !native || !universal) {
            throw new Error('Reload the mini app from the Telegram menu to update Phantom connection support.');
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
        if (!key()) throw new Error('Open the app from Telegram.');
        if (starting) return;
        starting = true;
        try {
            const saved = localStorage.getItem(key());
            const pending = saved ? JSON.parse(saved) : null;
            if (pending?.expiresAt * 1000 > Date.now() && pending.url
                && pending.purpose === purpose && pending.nonce === nonce) {
                if (pending.stage === 'signing') {
                    status('The connection was approved. Finish signing in Phantom, then return to Telegram.');
                    schedule();
                    return;
                }
                handoff(pending);
                status('Tap “Open Phantom app,” then approve the connection and signature.');
                schedule();
                return;
            }
            const result = await api('/wallet/mobile/start', { purpose, nonce });
            if (!result.sessionId || !Number.isFinite(result.expiresAt)
                || result.expiresAt * 1000 <= Date.now()) {
                throw new Error('Could not prepare a valid Phantom connection. Please try again.');
            }
            const next = { id: result.sessionId, purpose, nonce, expiresAt: result.expiresAt, url: result.url };
            handoff(next);
            try {
                localStorage.setItem(key(), JSON.stringify(next));
            } catch {
                clearHandoff();
                throw new Error('Could not save the connection on this device. Phantom was not opened; your balance was not changed.');
            }
            status('Ready to connect. Tap “Open Phantom app” below. Automatic browser redirects are disabled.');
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
                status('The Phantom approval expired. You can start the connection or withdrawal again.');
                return;
            }
            const result = await api('/wallet/mobile/status', { sessionId: pending.id });
            if (result.status === 'failed') {
                localStorage.removeItem(key());
                clearHandoff();
                status(result.error || 'Phantom approval was cancelled.');
                return;
            }
            if (result.status !== 'complete') {
                if (result.status === 'signing') {
                    localStorage.setItem(key(), JSON.stringify({ ...pending, stage: 'signing' }));
                    clearHandoff();
                    status('Phantom accepted the connection. Finish signing in Phantom, then return to Telegram.');
                } else if (pending.url) handoff(pending);
                schedule();
                return;
            }
            if (result.purpose !== pending.purpose) throw new Error('The Phantom signature purpose did not match.');
            const path = result.purpose === 'link' ? '/wallet/link' : '/wallet/withdraw';
            const completion = await api(path, { nonce: result.nonce, signature: result.signature, address: result.address });
            localStorage.removeItem(key());
            clearHandoff();
            status(result.purpose === 'link' ? 'Phantom connected. The signature was verified by the server.'
                : completion.status === 'processing'
                    ? 'The transaction is being checked on the network. Funds are reserved; it will not be sent again.'
                    : `Withdrawal submitted to Solana: ${completion.txHash}`);
            await window.secureWallet.refresh();
            if (result.purpose === 'link') alertUser('Phantom is connected to Zer0Life Run. You can withdraw ZRL and SOL.');
        } catch (error) {
            if (error.status >= 400 && error.status < 500) {
                localStorage.removeItem(key());
                clearHandoff();
                status(error.message);
                await window.secureWallet.refresh().catch(() => {});
            } else {
                status('The server is temporarily unavailable. Your approval is saved; checking again will not create a second payment.');
                schedule();
            }
        } finally {
            polling = false;
        }
    }
    async function checkWithdrawal(nonce) {
        const result = await api('/wallet/withdrawals/status', { nonce });
        status(result.status === 'confirmed' ? 'Withdrawal confirmed on Solana.'
            : result.status === 'failed' ? 'The network rejected the transfer; the amount was returned to your balance.'
                : 'Waiting for network confirmation. The amount remains reserved.');
        await window.secureWallet.refresh();
    }
    window.phantomMobile = { start, resume, checkWithdrawal };
    window.addEventListener('pageshow', resume);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) resume(); });
})();
