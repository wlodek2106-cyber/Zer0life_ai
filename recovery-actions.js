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
            ? 'Нет неперенесённых записей. Сохранённые оригиналы остаются на устройстве.'
            : result.recovery?.status === 'approved'
                ? 'Архив уже перенесён на основной баланс.'
                : 'Снимок архива сохранён на сервере. Перенос подтверждает владелец проекта.');
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
                title.textContent = `${record.username || 'Игрок'} · ${record.telegramId}`;
                const values = document.createElement('div');
                values.textContent = `К возврату: ${Number(record.totalZrl).toFixed(2)} ZRL и ${Number(record.totalSol).toFixed(9)} SOL. Старых ферм: ${record.farmCount}.`;
                const note = document.createElement('div');
                note.textContent = `Возврат вкладов ферм: ${Number(record.farmZrl).toFixed(2)} ZRL и ${Number(record.farmSol).toFixed(9)} SOL. Неполученный доход: +${Number(record.farmYieldZrl).toFixed(6)} ZRL.${record.yieldPendingCount ? ` Для ${record.yieldPendingCount} позиций нет подтверждённой ставки: их доход остаётся на сверке, не считается нулевым.` : ''}`;
                row.append(title, values, note);
                list.appendChild(row);
            });
            node('recovery-admin-apply').disabled = snapshots.length === 0;
            setStatus(snapshots.length ? `Готово к проверке заявок: ${snapshots.length}. Подтверждение разрешит тратить и выводить указанные суммы.`
                : 'Нет заявок для переноса. Игроку нужно открыть обновлённое приложение на устройстве со старой историей.');
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
            const approved = await confirm(`Вернуть ${zrl.toFixed(6)} ZRL и ${sol.toFixed(9)} SOL (${frozen.length} игроков)? Уже выплаченное исключено. Подтверждение разрешит тратить и выводить реальные средства проекта.`);
            if (!approved) {
                setStatus('Подтверждение отменено. Балансы не изменены.');
                return;
            }
            const result = await api('/wallet/recovery/admin/apply', { requests: frozen });
            credited = true;
            snapshots = [];
            node('recovery-admin-list').replaceChildren();
            await window.secureWallet.refresh();
            setStatus(`Перенос выполнен: ${result.receipts.length} счетов. Повторное подтверждение не начислит суммы ещё раз. Для вывода подключите Phantom и подтвердите подпись.`);
        } catch (error) {
            setStatus(credited
                ? 'Перенос выполнен, но не удалось обновить экран. Откройте Wallet заново — повторное начисление не требуется.'
                : error.message === 'WebAppPopupOpened'
                    ? 'В Telegram уже открыто другое окно. Закройте его и нажмите подтверждение ещё раз.'
                    : `Не удалось подтвердить перенос: ${error.message}. Балансы не изменены, если сервер ещё не принял запрос. При сетевой ошибке обновите Wallet перед повтором.`);
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
