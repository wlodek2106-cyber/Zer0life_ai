'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'community-feed.js'), 'utf8');
const walletSource = fs.readFileSync(path.join(__dirname, '..', 'wallet.js'), 'utf8');
const apiStart = source.indexOf('    async function api(');
const apiEnd = source.indexOf('\n\n    function make(', apiStart);
assert.notEqual(apiStart, -1, 'community api function exists');
assert.notEqual(apiEnd, -1, 'community api function has a following boundary');
const apiDefinition = source.slice(apiStart, apiEnd).trim();

function apiBaseFrom(text) {
    return text.match(/const API_BASE\s*=\s*['"]([^'"]+)['"]/)?.[1];
}

function createApi(fetchImpl) {
    const sandbox = {
        API_BASE: 'https://community.example.test',
        FEED_RETRY_DELAYS: [0, 0, 0],
        fetch: fetchImpl,
        initData: () => 'synthetic-session-for-test',
        window: { setTimeout: callback => { callback(); return 0; } },
    };
    vm.createContext(sandbox);
    vm.runInContext(`${apiDefinition}\nglobalThis.communityApi = api;`, sandbox);
    return sandbox.communityApi;
}

test('community feed uses the same API host as Wallet', () => {
    const communityBase = apiBaseFrom(source);
    const walletBase = apiBaseFrom(walletSource);
    assert.ok(communityBase, 'community API host is defined');
    assert.equal(communityBase, walletBase);
});

test('community feed reads retry transient failures without browser credentials', async () => {
    let requests = 0;
    const api = createApi(async (_url, options) => {
        requests += 1;
        assert.equal(options.mode, 'cors');
        assert.equal(options.credentials, 'omit');
        assert.equal(options.cache, 'no-store');
        assert.equal(JSON.parse(options.body).initData, 'synthetic-session-for-test');
        if (requests === 1) throw new TypeError('Load failed');
        if (requests === 2) {
            return { status: 503, ok: false, json: async () => ({ success: false }) };
        }
        return { status: 200, ok: true, json: async () => ({ success: true, posts: [] }) };
    });

    const result = await api('/wallet/community/feed', {}, { retryRead: true });

    assert.equal(requests, 3);
    assert.deepEqual(JSON.parse(JSON.stringify(result)), { success: true, posts: [] });
});

test('community post writes are never retried after an uncertain network failure', async () => {
    let requests = 0;
    const api = createApi(async () => {
        requests += 1;
        throw new TypeError('Load failed');
    });

    await assert.rejects(
        api('/wallet/community/posts', { content: 'Hello' }),
        /Could not confirm whether your post was saved/
    );
    assert.equal(requests, 1);
});
