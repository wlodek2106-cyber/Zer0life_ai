/* Read-only view of device-local farming records. Never credits or unlocks funds. */
(() => {
    'use strict';

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

    function render() {
        const container = document.getElementById('legacy-farming-deposits-list');
        const count = document.getElementById('legacy-farms-count');
        if (!container || !count) return;
        container.replaceChildren();
        const snapshot = readRecords();
        count.textContent = snapshot.error ? 'Ошибка чтения' : `${snapshot.records.length} · на проверке`;
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
            title.textContent = `Старая ферма №${index + 1} · Ожидает проверки`;
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
            }
            line(card, 'Статус', 'не включена в доступный баланс; начисление и вывод здесь недоступны');
            container.appendChild(card);
        });
    }

    window.legacyFarmReview = { render };
    window.addEventListener('storage', (event) => {
        if (typeof STORAGE_PREFIX !== 'undefined' && (event.key === null
            || event.key === STORAGE_PREFIX + 'legacy_pending_farms'
            || event.key === STORAGE_PREFIX + 'active_farms')) render();
    });
    render();
})();
