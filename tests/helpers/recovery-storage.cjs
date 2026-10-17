/* Synthetic browser data only. No browser, remote server or player export. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const NOW = 1800000000000;
const DAY = 86400000;
const prefix = id => `zrl_user_${id}_`;
const profiles = {
    200: {
        total_balance: '100.000001', earned_zrl: '90', unclaimed: '0',
        pool_deposit: '.008', casino_total_won: '.004', casino_turnover: '.002',
        active_farms: JSON.stringify([
            { id: 'shared-old-id', asset: 'zrl', amount: 1000, dailyRate: .01,
                startTime: NOW - 10 * DAY, endTime: NOW + 20 * DAY, claimedReward: 25 },
            { id: 'old-sol', asset: 'sol', amount: .002, dailyRate: 100,
                startTime: NOW - 10 * DAY, endTime: NOW + 20 * DAY, claimedReward: .5 },
        ]),
    },
    300: {
        total_balance: '60', earned_zrl: '60.123456', unclaimed: '0',
        pool_deposit: '.02', casino_total_won: '0', casino_turnover: '0',
        active_farms: JSON.stringify([
            { id: 'shared-old-id', asset: 'zrl', amount: 250, dailyRate: .02,
                startTime: NOW - 40 * DAY, endTime: NOW - 10 * DAY, claimedReward: 40 },
        ]),
    },
};
function initialStorage() {
    return new Map(Object.entries(profiles).flatMap(([id, values]) =>
        Object.entries(values).map(([key, value]) => [prefix(id) + key, value])));
}
function browser(storage, id, failAt = Infinity) {
    let writes = 0;
    const elements = new Map();
    class Element {
        constructor() { this.children = []; this.style = {}; this.textContent = ''; }
        appendChild(child) { this.children.push(child); }
        replaceChildren() { this.children = []; }
        get text() { return this.textContent + this.children.map(child => child.text).join('\n'); }
    }
    const context = {
        STORAGE_PREFIX: prefix(id),
        window: { addEventListener() {}, Telegram: { WebApp: { initData: `synthetic-${id}` } } },
        document: {
            getElementById(key) {
                if (!elements.has(key)) elements.set(key, new Element());
                return elements.get(key);
            },
            createElement: () => new Element(),
        },
        localStorage: {
            getItem: key => storage.has(key) ? storage.get(key) : null,
            setItem(key, value) {
                if (++writes === failAt) throw Error('Synthetic storage interruption');
                storage.set(key, String(value));
            },
        },
    };
    vm.createContext(context);
    const load = name => vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../..', name), 'utf8'), context, { filename: name });
    load('legacy-recovery.js');
    return { context, load, elements, api: context.window.legacyRecovery };
}
function snapshots() {
    const storage = initialStorage();
    const result = {};
    for (const id of Object.keys(profiles)) {
        const view = browser(storage, id);
        const amounts = view.api.preserve(prefix(id));
        result[id] = { ...amounts,
            farms: JSON.parse(storage.get(prefix(id) + 'legacy_pending_farms')) };
    }
    return result;
}
module.exports = { NOW, DAY, prefix, profiles, initialStorage, browser, snapshots };
if (require.main === module) process.stdout.write(JSON.stringify(snapshots()));
