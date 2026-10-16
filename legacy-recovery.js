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
                ? 'Ожидаем сервер' : `${(availableZrl + savedZrl).toFixed(2)} ZRL`);
            text('recovery-total-sol', savedSol === null || availableSol === null
                ? 'Ожидаем сервер' : `${(availableSol + savedSol).toFixed(9)} SOL`);
            text('recovery-split-zrl', savedZrl === null ? 'Архив требует проверки'
                : `${availableZrl === null ? '—' : availableZrl.toFixed(2)} доступно · ${savedZrl.toFixed(2)} сохранено в архиве`);
            text('recovery-split-sol', savedSol === null ? 'Архив требует проверки'
                : `${availableSol === null ? '—' : availableSol.toFixed(9)} доступно · ${savedSol.toFixed(9)} сохранено в архиве`);
            text('header-legacy-balance', savedZrl > 0 ? `Архив: ${savedZrl.toFixed(2)} ZRL · на проверке` : '');
            const mismatch = serverState && ((localZrl > 0 && localZrl !== savedZrl)
                || (localSol > 0 && localSol !== savedSol));
            text('recovery-status', warning || (serverState?.legacyBalanceRestored
                ? 'Архив перенесён на основной баланс. Историческая копия сохранена и повторно не начисляется.'
                : mismatch
                ? 'Локальная копия отличается от серверной и сохранена отдельно. Суммы не складываются повторно.'
                : serverState
                    ? 'Итог включает доступный баланс и старый архив. Архив пока не доступен для трат и вывода. Средства в Farming считаются отдельно.'
                    : 'Показана локальная история. Серверный баланс ещё не загружен; итог не рассчитан.'));
        } catch {
            text('recovery-status', 'Не удалось прочитать историю на устройстве. Не очищайте данные Telegram.');
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
