/* Read-only view of device-local farming records. Never credits or unlocks funds. */
(() => {
    'use strict';
    let restoredIds = new Set();
    let settlements = new Map();

    function savedNumber(value) {
        if (typeof value !== 'number' && typeof value !== 'string') return null;
        if (typeof value === 'string' && !value.trim()) return null;
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 ? number : null;
    }

    function readRecords() {
        if (typeof STORAGE_PREFIX === 'undefined') {
            return { error: 'Не удалось определить профиль. Старые записи не изменены.' };
        }
        try {
            const backup = localStorage.getItem(STORAGE_PREFIX + 'legacy_pending_farms');
            // Only fall back if no snapshot exists, never replace an existing snapshot.
            const raw = backup === null
                ? localStorage.getItem(STORAGE_PREFIX + 'active_farms')
                : backup;
            if (raw === null) return { records: [], missing: true };
            const records = JSON.parse(raw);
            if (!Array.isArray(records)) throw new Error('Invalid farming snapshot');
            return { records };
        } catch {
            return { error: 'Не удалось прочитать старые записи на этом устройстве. Данные не изменены. Не очищайте хранилище Telegram.' };
        }
    }

    function line(card, label, value) {
        const row = document.createElement('div');
        row.style.cssText = 'font-size:12px;color:#b5bdad;line-height:1.5;overflow-wrap:anywhere;';
        row.textContent = `${label}: ${value}`;
        card.appendChild(row);
    }

    function savedDate(value) {
        const timestamp = savedNumber(value);
        if (timestamp === null || timestamp === 0) return 'не указана';
        const date = new Date(timestamp);
        return Number.isFinite(date.getTime()) ? date.toLocaleDateString('ru-RU') : 'некорректная дата';
    }

    function income(record, now = Date.now()) {
        if (!record || typeof record !== 'object') return null;
        const amount = savedNumber(record.amount), rate = savedNumber(record.dailyRate);
        const start = savedNumber(record.startTime), end = savedNumber(record.endTime);
        const claimed = savedNumber(record.claimedReward);
        if ([amount, rate, start, end, claimed].some(value => value === null) || !start || end <= start) return null;
        const cutoff = settlements.get(String(record.id))?.cutoffMs || now;
        const elapsed = Math.max(0, Math.min(cutoff, end) - start) / 86400000;
        const earned = Math.max(0, amount * rate * elapsed - claimed);
        return Number.isFinite(earned) ? earned : null;
    }

    function render() {
        const container = document.getElementById('legacy-farming-deposits-list');
        const count = document.getElementById('legacy-farms-count');
        if (!container || !count) return;
        container.replaceChildren();
        const snapshot = readRecords();
        const totals = document.getElementById('legacy-farms-summary');
        if (totals) {
            if (snapshot.error || snapshot.missing || !snapshot.records.length) totals.textContent = 'Полный итог ферм пока недоступен: нужны сохранённые записи.';
            else {
                let zrl = 0, sol = 0, yieldZrl = 0, unknown = 0;
                snapshot.records.forEach(record => {
                    const amount = savedNumber(record?.amount);
                    if (amount !== null && record?.asset === 'zrl') zrl += amount;
                    if (amount !== null && record?.asset === 'sol') sol += amount;
                    const earned = income(record);
                    if (!settlements.get(String(record?.id))?.yieldReviewed) {
                        if (earned === null) unknown++;
                        else yieldZrl += earned;
                    }
                });
                totals.textContent = `Все сохранённые вклады: ${zrl.toLocaleString('ru-RU')} ZRL и ${sol.toLocaleString('ru-RU', { maximumFractionDigits: 9 })} SOL. Осталось сверить доход (локальная оценка): ${yieldZrl.toLocaleString('ru-RU', { maximumFractionDigits: 6 })} ZRL.${unknown ? ` Для ${unknown} записей расчёт пока невозможен — это не нулевой доход.` : ''} Это история и расчёт, а не дополнительный доступный баланс. Ранее полученные награды вычтены; для закрытых позиций доход ограничен датой закрытия.`;
            }
        }
        const pending = snapshot.records?.filter(record => !restoredIds.has(String(record?.id))).length || 0;
        count.textContent = snapshot.error ? 'Ошибка чтения' : `${pending} · на проверке`;
        if (snapshot.error || snapshot.records.length === 0) {
            const message = document.createElement('p');
            message.style.cssText = 'font-size:12px;color:#b5bdad;line-height:1.5;margin:0;';
            message.textContent = snapshot.error || (snapshot.missing
                ? 'На этом устройстве не найдена копия старых ферм. Это не подтверждает отсутствие вкладов на других устройствах.'
                : 'В сохранённой копии на этом устройстве нет старых ферм. Это не итог проверки счёта.');
            container.appendChild(message);
            return;
        }
        snapshot.records.forEach((record, index) => {
            const card = document.createElement('article');
            card.style.cssText = 'padding:12px;border-radius:14px;border:1px solid rgba(213,178,74,.3);background:linear-gradient(145deg,rgba(213,178,74,.08),rgba(10,14,10,.75));box-shadow:inset 0 1px 0 rgba(255,255,255,.05);';
            const title = document.createElement('strong');
            title.style.cssText = 'display:block;font-size:13px;color:#e5d29b;margin-bottom:5px;';
            const restored = restoredIds.has(String(record?.id));
            title.textContent = `Старая ферма №${index + 1} · ${restored ? 'Закрыта при восстановлении' : 'Ожидает проверки'}`;
            card.appendChild(title);
            if (!record || typeof record !== 'object' || Array.isArray(record)) {
                line(card, 'Запись', 'не удалось распознать; оригинал сохранён без изменений');
            } else {
                const asset = typeof record.asset === 'string' ? record.asset.toLowerCase() : '';
                const amount = savedNumber(record.amount);
                const months = savedNumber(record.months);
                line(card, 'Сохранённый вклад', amount !== null && ['sol', 'zrl'].includes(asset)
                    ? `${amount.toLocaleString('ru-RU', { maximumFractionDigits: 9 })} ${asset.toUpperCase()}`
                    : 'сумма или валюта требуют проверки');
                line(card, 'План', months === 300 ? 'Super Yield (старый код плана 300)'
                    : months !== null && months > 0 ? `${months} мес.` : 'не указан');
                line(card, 'Начало', savedDate(record.startTime));
                line(card, 'Окончание по записи', savedDate(record.endTime));
                const claimed = savedNumber(record.claimedReward);
                if (claimed !== null) {
                    line(card, 'Получено ранее по локальной записи', `${claimed.toLocaleString('ru-RU', { maximumFractionDigits: 6 })} ZRL`);
                }
                const earned = income(record);
                line(card, settlements.get(String(record?.id))?.yieldReviewed
                    ? 'Доход на дату закрытия · учтён в восстановлении'
                    : 'Неполученный доход по сохранённой ставке', earned === null
                    ? 'нужна исходная ставка и даты; доход не считается нулевым'
                    : `${earned.toLocaleString('ru-RU', { maximumFractionDigits: 6 })} ZRL · локальная оценка`);
            }
            line(card, 'Статус', restored
                ? 'Вклад возвращён и доход проверен. Повторное начисление исключено.'
                : settlements.get(String(record?.id))?.principalReturned
                    ? 'Вклад уже возвращён; неполученный доход ещё требует сверки.'
                    : 'Возврат вклада и неполученный доход требуют проверки; история не удалена.');
            container.appendChild(card);
        });
    }

    window.legacyFarmReview = {
        render,
        income,
        setState(state) {
            restoredIds = new Set((state.restoredLegacyFarmIds || []).map(String));
            settlements = new Map((state.legacyFarmSettlements || []).map(record => [String(record.id), record]));
            render();
        },
    };
    window.addEventListener('storage', (event) => {
        if (typeof STORAGE_PREFIX !== 'undefined' && (event.key === null
            || event.key === STORAGE_PREFIX + 'legacy_pending_farms'
            || event.key === STORAGE_PREFIX + 'active_farms')) render();
    });
    render();
})();
