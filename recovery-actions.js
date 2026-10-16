(() => {
    'use strict';
    let snapshots = [], submitting = false;
    const node = id => document.getElementById(id);
    const api = (path, body) => window.secureWallet.request(path, body);
    const confirm = message => new Promise(resolve => {
        if (window.Telegram?.WebApp?.showConfirm) window.Telegram.WebApp.showConfirm(message, resolve);
        else resolve(window.confirm(message));
    });
    const setStatus = text => { if (node('recovery-action-status')) node('recovery-action-status').textContent = text; };

    async function submit() {
        if (typeof STORAGE_PREFIX === 'undefined') return;
        const raw = localStorage.getItem(STORAGE_PREFIX + 'legacy_pending_farms');
        const farms = raw === null ? [] : JSON.parse(raw);
        const result = await api('/wallet/recovery/submit', { farms });
        setStatus(result.status === 'nothing_pending'
            ? 'No records are waiting for recovery. The original copies remain on the device.'
            : result.recovery?.status === 'approved'
                ? 'The archive has already been moved to the main balance.'
                : 'The archive snapshot is saved on the server. The project owner must approve the recovery.');
        return result;
    }

    async function load() {
        if (submitting) return;
        submitting = true;
        const button = node('recovery-admin-load');
        if (button) button.disabled = true;
        try {
            const result = await api('/wallet/recovery/admin/list');
            snapshots = result.requests;
            const list = node('recovery-admin-list');
            list.replaceChildren();
            snapshots.forEach(record => {
                const row = document.createElement('div');
                row.style.cssText = 'padding:10px;border:1px solid #34432b;border-radius:12px;margin:8px 0;font-size:11px;line-height:1.5;';
                const title = document.createElement('strong');
                title.textContent = `${record.username || 'Player'} · ${record.telegramId}`;
                const values = document.createElement('div');
                values.textContent = `To return: ${Number(record.totalZrl).toFixed(2)} ZRL and ${Number(record.totalSol).toFixed(9)} SOL. Legacy farms: ${record.farmCount}.`;
                const note = document.createElement('div');
                note.textContent = `Farm deposit refunds: ${Number(record.farmZrl).toFixed(2)} ZRL and ${Number(record.farmSol).toFixed(9)} SOL. Unclaimed income: +${Number(record.farmYieldZrl).toFixed(6)} ZRL.${record.yieldPendingCount ? ` ${record.yieldPendingCount} position(s) have no verified rate; income remains under review and is not treated as zero.` : ''}`;
                row.append(title, values, note);
                list.appendChild(row);
            });
            node('recovery-admin-apply').disabled = snapshots.length === 0;
            setStatus(snapshots.length ? `${snapshots.length} recovery request(s) are ready for review. Approval makes the listed amounts spendable and withdrawable.`
                : 'No recovery requests are waiting. The player must open the updated app on the device with the legacy history.');
        } catch (error) {
            setStatus(error.message);
        } finally {
            submitting = false;
            if (button) button.disabled = false;
        }
    }

    async function apply() {
        if (submitting || !snapshots.length) return;
        const frozen = snapshots.map(row => ({ requestId: row.requestId, digest: row.digest }));
        const zrl = snapshots.reduce((sum, row) => sum + Number(row.totalZrl), 0);
        const sol = snapshots.reduce((sum, row) => sum + Number(row.totalSol), 0);
        submitting = true;
        const applyButton = node('recovery-admin-apply');
        const loadButton = node('recovery-admin-load');
        if (applyButton) applyButton.disabled = true;
        if (loadButton) loadButton.disabled = true;
        let credited = false;
        try {
            // Telegram's real showConfirm/showPopup rejects messages >256 UTF-16 units.
            const approved = await confirm(`Restore ${zrl.toFixed(6)} ZRL and ${sol.toFixed(9)} SOL for ${frozen.length} players? Previously paid amounts are excluded. Approval makes real project funds spendable and withdrawable.`);
            if (!approved) {
                setStatus('Approval cancelled. Balances were not changed.');
                return;
            }
            const result = await api('/wallet/recovery/admin/apply', { requests: frozen });
            credited = true;
            snapshots = [];
            node('recovery-admin-list').replaceChildren();
            await window.secureWallet.refresh();
            setStatus(`Recovery completed for ${result.receipts.length} account(s). Repeating this action will not credit them again. Withdrawals are available from Wallet.`);
        } catch (error) {
            setStatus(credited
                ? 'Recovery completed, but the screen could not be refreshed. Reopen Wallet; do not submit recovery again.'
                : error.message === 'WebAppPopupOpened'
                    ? 'Another Telegram dialog is open. Close it and try again.'
                    : `Could not approve recovery: ${error.message}. Balances remain unchanged unless the server accepted the request. If the network timed out, refresh Wallet before retrying.`);
        } finally {
            submitting = false;
            if (applyButton) applyButton.disabled = snapshots.length === 0;
            if (loadButton) loadButton.disabled = false;
        }
    }

    window.recoveryActions = {
        submit,
        setState(state) {
            if (node('recovery-admin-panel')) node('recovery-admin-panel').hidden = !state.isRecoveryAdmin;
        },
    };
    node('recovery-admin-load')?.addEventListener('click', load);
    node('recovery-admin-apply')?.addEventListener('click', apply);
})();
