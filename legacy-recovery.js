/* Historical records remain separate from spendable funds and payout accounting. */
(() => {
    'use strict';
    let serverState = null;
    let warning = '';
    const rawKeys = ['total_balance', 'earned_zrl', 'unclaimed', 'pool_deposit',
        'casino_total_won', 'casino_turnover', 'active_farms', 'sol_balance',
        'usdc_balance', 'user_real_deposit'];
    const number = value => {
        const result = Number(value);
        return Number.isFinite(result) && result >= 0 ? result : null;
    };
    const text = (id, value) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    };

    function preserve(prefix) {
        // Capture original strings before dashboard initialization can save new
        // server values over them. Never overwrite an existing historical copy.
        const backupKey = prefix + 'legacy_recovery_backup_v1';
        if (localStorage.getItem(backupKey) === null) {
            const original = {};
            rawKeys.forEach(key => { original[key] = localStorage.getItem(prefix + key); });
            localStorage.setItem(backupKey, JSON.stringify(original));
        }
        if (localStorage.getItem(prefix + 'legacy_pending_farms') === null) {
            localStorage.setItem(prefix + 'legacy_pending_farms',
                localStorage.getItem(prefix + 'active_farms') ?? '[]');
        }
        const read = key => {
            const value = number(localStorage.getItem(prefix + key));
            if (value === null) throw new Error('Invalid historical amount');
            return value;
        };
        if (localStorage.getItem(prefix + 'legacy_pending_zrl') === null) {
            const value = Math.max(read('total_balance'), read('earned_zrl')) + read('unclaimed');
            localStorage.setItem(prefix + 'legacy_pending_zrl', String(value));
        }
        if (localStorage.getItem(prefix + 'legacy_pending_sol') === null) {
            const value = Math.max(0, read('pool_deposit') + read('casino_total_won') - read('casino_turnover'));
            localStorage.setItem(prefix + 'legacy_pending_sol', String(value));
        }
        return {
            zrl: read('legacy_pending_zrl'),
            sol: read('legacy_pending_sol'),
        };
    }

    function render() {
        if (typeof STORAGE_PREFIX === 'undefined') return;
        try {
            const localZrl = number(localStorage.getItem(STORAGE_PREFIX + 'legacy_pending_zrl'));
            const localSol = number(localStorage.getItem(STORAGE_PREFIX + 'legacy_pending_sol'));
            const savedZrl = serverState ? number(serverState.legacyPendingZrl) : localZrl;
            const savedSol = serverState ? number(serverState.legacyPendingSol) : localSol;
            const availableZrl = serverState ? number(serverState.availableZrl) : null;
            const availableSol = serverState ? number(serverState.gameSolBalance) : null;
            text('recovery-total-zrl', savedZrl === null || availableZrl === null
                ? 'Waiting for server' : `${(availableZrl + savedZrl).toFixed(2)} ZRL`);
            text('recovery-total-sol', savedSol === null || availableSol === null
                ? 'Waiting for server' : `${(availableSol + savedSol).toFixed(9)} SOL`);
            text('recovery-split-zrl', savedZrl === null ? 'Archive needs review'
                : `${availableZrl === null ? '—' : availableZrl.toFixed(2)} available · ${savedZrl.toFixed(2)} saved in archive`);
            text('recovery-split-sol', savedSol === null ? 'Archive needs review'
                : `${availableSol === null ? '—' : availableSol.toFixed(9)} available · ${savedSol.toFixed(9)} saved in archive`);
            text('header-legacy-balance', savedZrl > 0 ? `Archive: ${savedZrl.toFixed(2)} ZRL · under review` : '');
            const mismatch = serverState && ((localZrl > 0 && localZrl !== savedZrl)
                || (localSol > 0 && localSol !== savedSol));
            text('recovery-status', warning || (serverState?.legacyBalanceRestored
                ? 'The archive was moved to the main balance. The historical copy is preserved and will not be credited again.'
                : mismatch
                ? 'The local copy differs from the server copy and remains stored separately. Amounts are not added twice.'
                : serverState
                    ? 'The total includes the available balance and legacy archive. Archived funds are not yet available to spend or withdraw. Farming funds are tracked separately.'
                    : 'Showing local history. The server balance has not loaded yet, so no total is available.'));
        } catch {
            text('recovery-status', 'Could not read the history on this device. Do not clear Telegram data.');
        }
    }
    window.legacyRecovery = {
        preserve,
        render,
        setState(value) { serverState = value; render(); },
        setWarning(value) { warning = value; render(); },
    };
    window.addEventListener('storage', render);
})();
