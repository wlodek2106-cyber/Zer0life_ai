const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../legacy-farms.js'), 'utf8');

class Element {
    constructor(tag = 'div') { this.tag = tag; this.children = []; this.style = {}; this.textContent = ''; }
    appendChild(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    get text() { return this.textContent + this.children.map(child => child.text).join('\n'); }
}
function view(entries, prefix = 'zrl_user_test_') {
    const storage = new Map(Object.entries(entries));
    const before = [...storage];
    const list = new Element();
    const count = new Element();
    const listeners = {};
    const window = { addEventListener(type, fn) { listeners[type] = fn; } };
    const context = {
        window, STORAGE_PREFIX: prefix,
        localStorage: {
            getItem(key) { return storage.has(key) ? storage.get(key) : null; },
            setItem() { throw Error('Read-only view attempted a write'); },
            removeItem() { throw Error('Read-only view attempted a delete'); },
        },
        document: {
            getElementById(id) { return id === 'legacy-farming-deposits-list' ? list : id === 'legacy-farms-count' ? count : null; },
            createElement(tag) { return new Element(tag); },
        },
    };
    vm.runInNewContext(source, context);
    assert.deepEqual([...storage], before);
    return { list, count, context, listeners, storage };
}
const farm = { id: 'old-test', asset: 'sol', amount: .007, months: 12, startTime: 1760000000000, endTime: 1791104000000, claimedReward: 15 };
const key = 'zrl_user_test_legacy_pending_farms';

test('shows saved principal, dates and recorded rewards with pending status, without actions', () => {
    const { list, count } = view({ [key]: JSON.stringify([farm]) });
    assert.equal(count.text, '1 · на проверке');
    assert.match(list.text, /0,007 SOL/);
    assert.match(list.text, /12 мес/);
    assert.match(list.text, /15 ZRL/);
    assert.match(list.text, /Ожидает проверки/);
    const descendants = node => [node, ...node.children.flatMap(descendants)];
    assert(!descendants(list).some(node => ['button', 'a', 'input'].includes(node.tag)));
});
test('missing storage explains device-local limitation instead of claiming there were no farms', () => {
    assert.match(view({}).list.text, /не подтверждает отсутствие вкладов/);
});
test('an empty snapshot never falls back to a different active list', () => {
    const result = view({ [key]: '[]', zrl_user_test_active_farms: JSON.stringify([farm]) });
    assert.equal(result.count.text, '0 · на проверке');
    assert.equal(result.list.children.length, 1);
    assert.match(result.list.text, /нет старых ферм/);
});
test('falls back to original local records only if snapshot does not exist', () => {
    assert.match(view({ zrl_user_test_active_farms: JSON.stringify([farm]) }).list.text, /0,007 SOL/);
});
test('malformed JSON and non-array snapshots remain unchanged and show errors', () => {
    for (const raw of ['{broken', '{}', 'null', '"unexpected"']) {
        const result = view({ [key]: raw, zrl_user_test_active_farms: JSON.stringify([farm]) });
        assert.match(result.count.text, /Ошибка/);
        assert.match(result.list.text, /Данные не изменены/);
        assert.equal(result.storage.get(key), raw);
    }
});
test('invalid records are visible as requiring review, never silently discarded', () => {
    const result = view({ [key]: JSON.stringify([null, 5, {}, { amount: -100, asset: 'zrl' }]) });
    assert.equal(result.list.children.length, 4);
    assert.match(result.list.text, /не удалось распознать/);
    assert.match(result.list.text, /требуют проверки/);
});
test('untrusted strings are never used as markup or converted into wallet actions', () => {
    const result = view({ [key]: JSON.stringify([{ ...farm, id: "');withdraw()", asset: '<img onerror=alert(1)>', amount: '<script>', months: null }]) });
    assert.doesNotMatch(result.list.text, /<img|<script>|withdraw\(\)/);
    assert.match(result.list.text, /требуют проверки/);
});
test('renders once per record after repeated refreshes; a different profile cannot see the snapshot', () => {
    const result = view({ [key]: JSON.stringify([farm]) });
    result.context.window.legacyFarmReview.render();
    assert.equal(result.list.children.length, 1);
    result.context.STORAGE_PREFIX = 'zrl_user_other_';
    result.context.window.legacyFarmReview.render();
    assert.match(result.list.text, /не найдена копия/);
    assert.doesNotMatch(result.list.text, /0,007 SOL/);
});
test('super yield is not incorrectly displayed as a 300-month plan', () => {
    const result = view({ [key]: JSON.stringify([{ ...farm, months: 300 }]) });
    assert.match(result.list.text, /Super Yield/);
    assert.doesNotMatch(result.list.text, /300 мес/);
});
test('dashboard wires read-only section and renderer without changing financial routes', () => {
    const html = fs.readFileSync(path.join(__dirname, '../dashboard.html'), 'utf8');
    const wallet = fs.readFileSync(path.join(__dirname, '../wallet.js'), 'utf8');
    assert.equal((html.match(/id="legacy-farming-review"/g) || []).length, 1);
    assert(html.indexOf('src="./legacy-farms.js?') < html.indexOf('src="./wallet.js?'));
    assert(html.includes('window.legacyFarmReview?.render();'));
    assert(html.includes('let activeFarms = [];'));
    assert(html.includes('if (!verifiedFarmsLoaded)'));
    assert(wallet.includes('verifiedFarmsLoaded = true'));
    assert(wallet.includes("byId('farming-modal')?.style.display === 'flex'"));
    assert(!/\bfetch\s*\(|secureWallet|calculateFarmRewards|setItem\s*\(/.test(source));
});
