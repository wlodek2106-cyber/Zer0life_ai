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
            return { error: 'Could not identify the profile. Existing records were not changed.' };
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
            return { error: 'Could not read legacy farming records on this device. No data was changed. Do not clear Telegram storage.' };
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
        if (timestamp === null || timestamp === 0) return 'Not specified';
        const date = new Date(timestamp);
        return Number.isFinite(date.getTime()) ? date.toLocaleDateString('en-US') : 'Invalid date';
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
            if (snapshot.error || snapshot.missing || !snapshot.records.length) totals.textContent = 'The full farming total is unavailable until the saved records are available.';
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
                totals.textContent = `All saved deposits: ${zrl.toLocaleString('en-US')} ZRL and ${sol.toLocaleString('en-US', { maximumFractionDigits: 9 })} SOL. Income still to reconcile (local estimate): ${yieldZrl.toLocaleString('en-US', { maximumFractionDigits: 6 })} ZRL.${unknown ? ` Income cannot yet be calculated for ${unknown} record(s); this does not mean the income is zero.` : ''} This is historical information and an estimate, not additional spendable balance. Previously received rewards are excluded; income for closed positions is limited to the closing date.`;
            }
        }
        const pending = snapshot.records?.filter(record => !restoredIds.has(String(record?.id))).length || 0;
        count.textContent = snapshot.error ? 'Read error' : `${pending} · under review`;
        if (snapshot.error || snapshot.records.length === 0) {
            const message = document.createElement('p');
            message.style.cssText = 'font-size:12px;color:#b5bdad;line-height:1.5;margin:0;';
            message.textContent = snapshot.error || (snapshot.missing
                ? 'No legacy farm copy was found on this device. This does not prove that there are no deposits on other devices.'
                : 'The saved copy on this device contains no legacy farms. This is not a final account review.');
            container.appendChild(message);
            return;
        }
        snapshot.records.forEach((record, index) => {
            const card = document.createElement('article');
            card.style.cssText = 'padding:12px;border-radius:14px;border:1px solid rgba(213,178,74,.3);background:linear-gradient(145deg,rgba(213,178,74,.08),rgba(10,14,10,.75));box-shadow:inset 0 1px 0 rgba(255,255,255,.05);';
            const title = document.createElement('strong');
            title.style.cssText = 'display:block;font-size:13px;color:#e5d29b;margin-bottom:5px;';
            const restored = restoredIds.has(String(record?.id));
            title.textContent = `Legacy Farm #${index + 1} · ${restored ? 'Closed during recovery' : 'Awaiting review'}`;
            card.appendChild(title);
            if (!record || typeof record !== 'object' || Array.isArray(record)) {
                line(card, 'Record', 'Could not read; the original was preserved unchanged');
            } else {
                const asset = typeof record.asset === 'string' ? record.asset.toLowerCase() : '';
                const amount = savedNumber(record.amount);
                const months = savedNumber(record.months);
                line(card, 'Saved deposit', amount !== null && ['sol', 'zrl'].includes(asset)
                    ? `${amount.toLocaleString('en-US', { maximumFractionDigits: 9 })} ${asset.toUpperCase()}`
                    : 'Amount or asset needs review');
                line(card, 'Plan', months === 300 ? 'Super Yield (legacy plan code 300)'
                    : months !== null && months > 0 ? `${months} months` : 'Not specified');
                line(card, 'Start date', savedDate(record.startTime));
                line(card, 'Recorded end date', savedDate(record.endTime));
                const claimed = savedNumber(record.claimedReward);
                if (claimed !== null) {
                    line(card, 'Previously received (local record)', `${claimed.toLocaleString('en-US', { maximumFractionDigits: 6 })} ZRL`);
                }
                const earned = income(record);
                line(card, settlements.get(String(record?.id))?.yieldReviewed
                    ? 'Income through closing date · included in recovery'
                    : 'Unclaimed income at saved rate', earned === null
                    ? 'Original rate and dates are needed; income is not assumed to be zero'
                    : `${earned.toLocaleString('en-US', { maximumFractionDigits: 6 })} ZRL · local estimate`);
            }
            line(card, 'Status', restored
                ? 'Deposit returned and income reviewed. Duplicate credit is prevented.'
                : settlements.get(String(record?.id))?.principalReturned
                    ? 'Deposit already returned; unclaimed income still needs review.'
                    : 'Deposit return and unclaimed income need review. The history has not been deleted.');
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
