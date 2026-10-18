'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const dashboard = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
const controller = fs.readFileSync(path.join(__dirname, '..', 'social-chat.js'), 'utf8');
const communityController = fs.readFileSync(path.join(__dirname, '..', 'community-feed.js'), 'utf8');
const navStart = dashboard.indexOf('<!-- BOTTOM NAVIGATION -->');
const navEnd = dashboard.indexOf('<script src="./legacy-recovery.js"', navStart);
const nav = dashboard.slice(navStart, navEnd);

test('Chat replaces DAO and opens the existing community screen', () => {
    assert.match(nav, /id="nav-chat"[^>]*aria-label="Open community feed, friends and private chats"/);
    assert.match(nav, />\s*Chat\s*<\/button>/);
    assert.doesNotMatch(nav, /id="nav-dao"|>\s*DAO\s*</);
    assert.match(communityController, /getElementById\('nav-chat'\)\?\.addEventListener\('click', openFeed\)/);
    assert.match(dashboard, /<script src="\.\/community-feed\.js\?v=zerolife-social-v2"><\/script>\s*<script src="\.\/social-chat\.js\?v=zerolife-social-v2"><\/script>/);
});

test('one screen exposes feed, friends, requests, and private chats', () => {
    for (const tab of ['feed', 'friends', 'chats']) {
        assert.match(dashboard, new RegExp(`data-social-tab="${tab}"`));
        assert.match(dashboard, new RegExp(`data-social-panel="${tab}"`));
    }
    for (const hook of [
        'social-player-search',
        'social-player-query',
        'social-player-results',
        'social-incoming-requests',
        'social-outgoing-requests',
        'social-friends-list',
        'social-chat-list',
        'social-chat-thread',
        'social-chat-messages',
        'social-chat-composer',
        'social-chat-input'
    ]) {
        assert.match(dashboard, new RegExp(`id="${hook}"`), `missing social hook ${hook}`);
    }
    assert.match(dashboard, /id="community-post-text"/);
    assert.match(dashboard, /id="community-feed-list"/);
    assert.match(dashboard, /maxlength="1000" placeholder="Send a quick check-in/);
});

test('community exposes profile, follows, and both public feed scopes', () => {
    for (const hook of ['social-profile-view', 'social-profile-content', 'social-profile-posts', 'social-followers-sheet', 'social-profile-more']) {
        assert.match(dashboard, new RegExp(`id="${hook}"`), `missing profile hook ${hook}`);
    }
    assert.match(dashboard, /data-feed-scope="latest"/);
    assert.match(dashboard, /data-feed-scope="following"/);
    assert.match(controller, /\/wallet\/community\/profile/);
    assert.match(controller, /\/wallet\/community\/follow/);
    assert.match(controller, /\/wallet\/community\/follows\/list/);
    assert.match(controller, /\/wallet\/community\/posts\/repost/);
    assert.match(communityController, /\/wallet\/community\/posts\/repost/);
    assert.match(communityController, /scope: state\.scope/);
});

test('signed-in identity and selected runner profile remain separate', () => {
    assert.match(controller, /myProfile:\s*null/);
    assert.match(controller, /state\.myProfile = result\.profile/);
    assert.match(controller, /state\.profile = result\.profile/);
    assert.match(controller, /state\.myProfile\?\.publicId/);
    assert.match(controller, /post\.reply\?\.content/);
});

test('profile and social content use safe text nodes for runner-provided copy', () => {
    assert.match(controller, /function node\(tag, className, text\)/);
    assert.match(controller, /el\.textContent = text/);
    assert.doesNotMatch(controller, /innerHTML\s*=/);
    assert.match(communityController, /authorButton\.dataset\.openProfile/);
});

function createSocialApi(fetchImpl, initData = 'synthetic-telegram-session') {
    const start = controller.indexOf('    async function api(');
    const end = controller.indexOf('\n\n    function setCard(', start);
    assert.notEqual(start, -1, 'social API function exists');
    assert.notEqual(end, -1, 'social API function boundary exists');
    const definition = controller.slice(start, end).trim();
    const sandbox = {
        API_BASE: 'https://community.example.test',
        fetch: fetchImpl,
        window: { Telegram: { WebApp: { initData } } },
        initData: () => initData,
        JSON,
        String,
        Error
    };
    vm.createContext(sandbox);
    vm.runInContext(`${definition}\nglobalThis.socialApi = api;`, sandbox);
    return sandbox.socialApi;
}

test('private social requests use signed Telegram data and omit browser credentials', async () => {
    let calls = 0;
    const api = createSocialApi(async (url, options) => {
        calls += 1;
        assert.equal(url, 'https://community.example.test/wallet/social/messages/send');
        assert.equal(options.credentials, 'omit');
        assert.equal(options.cache, 'no-store');
        assert.equal(JSON.parse(options.body).initData, 'synthetic-telegram-session');
        assert.equal(JSON.parse(options.body).senderId, undefined);
        return { ok: true, status: 201, json: async () => ({ success: true }) };
    });
    const result = await api('/wallet/social/messages/send', {
        peerPublicId: 'opaque-player-id',
        content: 'Hello',
        clientMessageId: 'message-once'
    });
    assert.equal(calls, 1);
    assert.equal(result.success, true);
});

test('uncertain private-message writes are never retried automatically', async () => {
    let calls = 0;
    const api = createSocialApi(async () => {
        calls += 1;
        throw new TypeError('network unavailable');
    });
    await assert.rejects(
        api('/wallet/social/messages/send', {
            peerPublicId: 'opaque-player-id',
            content: 'Hello',
            clientMessageId: 'message-once'
        }),
        /Could not reach the community server/
    );
    assert.equal(calls, 1);
});
