(() => {
    'use strict';

    const API_BASE = 'https://zer0life-ai-iz5n.onrender.com';
    const MESSAGE_LIMIT = 1000;
    const screen = document.getElementById('community-screen');
    if (!screen) return;

    const state = {
        profile: null,
        friends: [],
        incoming: [],
        outgoing: [],
        selectedPeer: null,
        searchResults: [],
        searchQuery: '',
        loaded: false,
        stateLoading: false,
        threadLoading: false,
        pendingSend: null
    };

    const $ = (selector, root = screen) => root.querySelector(selector);
    const $$ = (selector, root = screen) => Array.from(root.querySelectorAll(selector));
    const tabButtons = $$('[data-social-tab]');
    const tabPanels = $$('[data-social-panel]');
    const searchForm = $('#social-player-search');
    const searchInput = $('#social-player-query');
    const searchHint = $('#social-search-hint');
    const chatForm = $('#social-chat-composer');
    const chatInput = $('#social-chat-input');
    const chatStatus = $('#social-chat-status');

    function initData() {
        return String(window.Telegram?.WebApp?.initData || '');
    }

    function displayName() {
        try {
            const saved = typeof username !== 'undefined' ? username : '';
            return String(saved || '').trim().slice(0, 48) || 'Runner';
        } catch {
            return 'Runner';
        }
    }

    async function api(path, payload = {}) {
        const signedData = initData();
        if (!signedData) throw new Error('Open Zer0Life Run from Telegram to use friends and private chats.');

        let response;
        try {
            response = await fetch(API_BASE + path, {
                method: 'POST',
                mode: 'cors',
                credentials: 'omit',
                cache: 'no-store',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ initData: signedData, ...payload })
            });
        } catch {
            throw new Error('Could not reach the community server. Check your connection and try again.');
        }

        let result;
        try {
            result = await response.json();
        } catch {
            throw new Error('The community service returned an unreadable response.');
        }
        if (!response.ok || result?.success === false) {
            throw new Error(result?.error || result?.message || 'Could not complete that request. Try again.');
        }
        return result;
    }

    function setCard(container, name, visible) {
        container?.querySelectorAll(`[data-social-state="${name}"]`).forEach(node => {
            node.hidden = !visible;
        });
    }

    function clearRendered(container) {
        container?.querySelectorAll('[data-social-rendered="true"]').forEach(node => node.remove());
    }

    function cloneTemplate(id) {
        const template = document.getElementById(id);
        return template?.content?.firstElementChild?.cloneNode(true) || null;
    }

    function avatarInitial(name) {
        return Array.from(String(name || 'Runner').trim())[0]?.toUpperCase() || 'R';
    }

    function setAvatar(node, name) {
        if (node) node.textContent = avatarInitial(name);
    }

    function timeLabel(timestamp) {
        if (!timestamp) return '';
        const date = new Date(timestamp);
        if (Number.isNaN(date.getTime())) return '';
        const age = Math.max(0, Date.now() - date.getTime());
        if (age < 60_000) return 'Just now';
        if (age < 3_600_000) return `${Math.floor(age / 60_000)}m ago`;
        if (age < 86_400_000) return `${Math.floor(age / 3_600_000)}h ago`;
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }

    function clockLabel(timestamp) {
        const date = new Date(timestamp);
        return Number.isNaN(date.getTime())
            ? ''
            : date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    }

    function setChatStatus(message = '', isError = false) {
        if (!chatStatus) return;
        chatStatus.textContent = message;
        chatStatus.classList.toggle('is-error', Boolean(message && isError));
    }

    function setSearchHint(message, isError = false) {
        if (!searchHint) return;
        searchHint.textContent = message;
        searchHint.classList.toggle('is-error', Boolean(message && isError));
    }

    function setTab(name) {
        tabButtons.forEach(button => {
            const active = button.dataset.socialTab === name;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', String(active));
            button.tabIndex = active ? 0 : -1;
        });
        tabPanels.forEach(panel => {
            const active = panel.dataset.socialPanel === name;
            panel.classList.toggle('is-active', active);
            panel.hidden = !active;
        });
        if (name !== 'feed' && !state.loaded) refreshState();
    }

    function updateBadges() {
        const friendCount = $('[data-social-count="friends"]');
        const unreadCount = $('[data-social-count="unread"]');
        const unread = state.friends.reduce((sum, friend) => sum + Number(friend.unreadCount || 0), 0);
        if (friendCount) friendCount.textContent = state.friends.length ? String(state.friends.length) : '';
        if (unreadCount) unreadCount.textContent = unread ? String(unread) : '';
    }

    function renderSearchResults(results, query = state.searchQuery) {
        const container = $('#social-player-results');
        if (!container) return;
        clearRendered(container);
        setCard(container, 'search-loading', false);
        setCard(container, 'search-error', false);
        setCard(container, 'search-empty', results.length === 0);
        const emptyText = container.querySelector('[data-social-state="search-empty"] p');
        if (emptyText) {
            emptyText.textContent = query
                ? 'No runners found. Try their name or public ID.'
                : 'Runner profiles will appear here after they open the social screen.';
        }
        results.forEach(player => {
            const row = cloneTemplate('social-player-result-template');
            if (!row) return;
            row.dataset.socialRendered = 'true';
            row.dataset.publicId = player.publicId;
            setAvatar(row.querySelector('[data-social-avatar]'), player.displayName);
            row.querySelector('[data-social-display-name]').textContent = player.displayName;
            row.querySelector('[data-social-player-tag]').textContent = player.tag || '';
            row.querySelector('[data-social-action="send-request"]').dataset.publicId = player.publicId;
            container.appendChild(row);
        });
    }

    function renderRequests(containerId, templateId, requests, emptyState, loadingState, errorState, incoming) {
        const container = document.getElementById(containerId);
        if (!container) return;
        clearRendered(container);
        setCard(container, loadingState, false);
        setCard(container, errorState, false);
        setCard(container, emptyState, requests.length === 0);
        requests.forEach(item => {
            const row = cloneTemplate(templateId);
            if (!row) return;
            row.dataset.socialRendered = 'true';
            row.dataset.requestId = item.requestId;
            row.dataset.publicId = item.publicId;
            setAvatar(row.querySelector('[data-social-avatar]'), item.displayName);
            row.querySelector('[data-social-display-name]').textContent = item.displayName;
            const message = row.querySelector('[data-social-request-message]');
            if (message) message.textContent = item.message || (incoming ? 'Wants to connect with you.' : 'Waiting for a response.');
            const time = row.querySelector('[data-social-created-at]');
            if (time) time.textContent = timeLabel(item.createdAt);
            container.appendChild(row);
        });
    }

    function renderPeople() {
        const friendsList = $('#social-friends-list');
        const chatList = $('#social-chat-list');
        if (friendsList) {
            clearRendered(friendsList);
            setCard(friendsList, 'friends-loading', false);
            setCard(friendsList, 'friends-error', false);
            setCard(friendsList, 'friends-empty', state.friends.length === 0);
        }
        if (chatList) {
            clearRendered(chatList);
            setCard(chatList, 'chats-loading', false);
            setCard(chatList, 'chats-error', false);
            setCard(chatList, 'chats-empty', state.friends.length === 0);
        }

        state.friends.forEach(friend => {
            const friendRow = cloneTemplate('social-friend-template');
            if (friendRow && friendsList) {
                friendRow.dataset.socialRendered = 'true';
                friendRow.dataset.publicId = friend.publicId;
                setAvatar(friendRow.querySelector('[data-social-avatar]'), friend.displayName);
                friendRow.querySelector('[data-social-display-name]').textContent = friend.displayName;
                friendRow.querySelector('[data-social-last-message]').textContent = friend.lastMessage || 'Connected — start a conversation';
                const badge = friendRow.querySelector('[data-social-unread]');
                if (friend.unreadCount > 0) {
                    badge.hidden = false;
                    badge.textContent = String(friend.unreadCount);
                }
                friendRow.querySelector('[data-social-action="open-chat"]').dataset.publicId = friend.publicId;
                friendsList.appendChild(friendRow);
            }

            const chatRow = cloneTemplate('social-chat-item-template');
            if (chatRow && chatList) {
                chatRow.dataset.socialRendered = 'true';
                chatRow.dataset.publicId = friend.publicId;
                chatRow.classList.toggle('is-selected', state.selectedPeer?.publicId === friend.publicId);
                setAvatar(chatRow.querySelector('[data-social-avatar]'), friend.displayName);
                chatRow.querySelector('[data-social-display-name]').textContent = friend.displayName;
                chatRow.querySelector('[data-social-last-message]').textContent = friend.lastMessage || 'Start a conversation';
                const time = chatRow.querySelector('[data-social-last-message-at]');
                if (time) time.textContent = timeLabel(friend.lastMessageAt);
                const badge = chatRow.querySelector('[data-social-unread]');
                if (friend.unreadCount > 0) {
                    badge.hidden = false;
                    badge.textContent = String(friend.unreadCount);
                }
                chatList.appendChild(chatRow);
            }
        });
        updateBadges();
    }

    function renderSocialState() {
        renderPeople();
        renderRequests(
            'social-incoming-requests',
            'social-incoming-request-template',
            state.incoming,
            'incoming-empty',
            'incoming-loading',
            'incoming-error',
            true
        );
        renderRequests(
            'social-outgoing-requests',
            'social-outgoing-request-template',
            state.outgoing,
            'outgoing-empty',
            'outgoing-loading',
            'outgoing-error',
            false
        );
    }

    function showStateLoading() {
        [
            ['social-friends-list', 'friends-loading', 'friends-empty', 'friends-error'],
            ['social-chat-list', 'chats-loading', 'chats-empty', 'chats-error'],
            ['social-incoming-requests', 'incoming-loading', 'incoming-empty', 'incoming-error'],
            ['social-outgoing-requests', 'outgoing-loading', 'outgoing-empty', 'outgoing-error']
        ].forEach(([id, loading, empty, error]) => {
            const container = document.getElementById(id);
            setCard(container, loading, true);
            setCard(container, empty, false);
            setCard(container, error, false);
        });
    }

    function showStateError() {
        [
            ['social-friends-list', 'friends-loading', 'friends-empty', 'friends-error'],
            ['social-chat-list', 'chats-loading', 'chats-empty', 'chats-error'],
            ['social-incoming-requests', 'incoming-loading', 'incoming-empty', 'incoming-error'],
            ['social-outgoing-requests', 'outgoing-loading', 'outgoing-empty', 'outgoing-error']
        ].forEach(([id, loading, empty, error]) => {
            const container = document.getElementById(id);
            setCard(container, loading, false);
            setCard(container, empty, false);
            setCard(container, error, true);
        });
    }

    async function refreshState({ quiet = false, skipThread = false } = {}) {
        if (state.stateLoading) return;
        state.stateLoading = true;
        if (!state.loaded && !quiet) showStateLoading();
        try {
            const result = await api('/wallet/social/state', { displayName: displayName() });
            state.profile = result.profile;
            state.friends = Array.isArray(result.friends) ? result.friends : [];
            state.incoming = Array.isArray(result.incomingRequests) ? result.incomingRequests : [];
            state.outgoing = Array.isArray(result.outgoingRequests) ? result.outgoingRequests : [];
            state.loaded = true;
            renderSocialState();
            if (state.selectedPeer) {
                const currentFriend = state.friends.find(friend => friend.publicId === state.selectedPeer.publicId);
                if (currentFriend) state.selectedPeer = currentFriend;
                if (!skipThread && document.body.classList.contains('is-community-open')) {
                    await loadThread({ quiet: true });
                }
            }
        } catch (error) {
            if (!state.loaded || !quiet) showStateError();
            if (state.selectedPeer) setChatStatus(error.message, true);
        } finally {
            state.stateLoading = false;
        }
    }

    async function searchPlayers(query = searchInput?.value || '') {
        const cleaned = String(query).trim();
        state.searchQuery = cleaned;
        state.searchResults = [];
        if (cleaned.length < 2) {
            setSearchHint('Enter at least two characters to search.', true);
            renderSearchResults([], '');
            return;
        }
        setSearchHint('Looking for runners…');
        setCard($('#social-player-results'), 'search-empty', false);
        setCard($('#social-player-results'), 'search-error', false);
        setCard($('#social-player-results'), 'search-loading', true);
        try {
            const result = await api('/wallet/social/search', { query: cleaned });
            state.searchResults = Array.isArray(result.players) ? result.players : [];
            renderSearchResults(state.searchResults, cleaned);
            setSearchHint(`${state.searchResults.length} runner${state.searchResults.length === 1 ? '' : 's'} found.`);
        } catch (error) {
            const container = $('#social-player-results');
            setCard(container, 'search-loading', false);
            setCard(container, 'search-empty', false);
            setCard(container, 'search-error', true);
            setSearchHint(error.message, true);
        }
    }

    function selectConversation(peer) {
        if (!peer?.publicId) return;
        state.selectedPeer = {
            publicId: peer.publicId,
            displayName: peer.displayName || 'Runner',
            unreadCount: 0
        };
        setTab('chats');
        renderPeople();
        const placeholder = $('.social-chat-placeholder');
        const selected = $('[data-social-selected]');
        const loading = $('[data-social-state="chat-loading"]');
        const error = $('[data-social-state="chat-error"]');
        if (placeholder) placeholder.hidden = true;
        if (selected) selected.hidden = false;
        if (loading) loading.hidden = false;
        if (error) error.hidden = true;
        const title = $('[data-social-peer-name]');
        const avatar = $('[data-social-peer-avatar]');
        if (title) title.textContent = state.selectedPeer.displayName;
        setAvatar(avatar, state.selectedPeer.displayName);
        if (chatInput) chatInput.disabled = true;
        const sendButton = chatForm?.querySelector('button[type="submit"]');
        if (sendButton) sendButton.disabled = true;
        setChatStatus('Loading conversation…');
        loadThread();
    }

    function renderMessages(messages) {
        const container = $('#social-chat-messages');
        if (!container) return;
        clearRendered(container);
        const empty = container.querySelector('[data-social-state="messages-empty"]');
        if (empty) empty.hidden = messages.length !== 0;
        messages.forEach(message => {
            const row = cloneTemplate('social-chat-message-template');
            if (!row) return;
            row.dataset.socialRendered = 'true';
            row.dataset.fromMe = String(Boolean(message.fromMe));
            row.classList.toggle('is-mine', Boolean(message.fromMe));
            row.querySelector('[data-social-message-sender]').textContent = message.fromMe
                ? 'YOU'
                : state.selectedPeer?.displayName || 'RUNNER';
            row.querySelector('[data-social-message-content]').textContent = message.content;
            row.querySelector('[data-social-message-time]').textContent = clockLabel(message.createdAt);
            container.appendChild(row);

            if (
                state.pendingSend
                && message.fromMe
                && message.clientMessageId === state.pendingSend.clientMessageId
            ) {
                state.pendingSend = null;
                if (chatInput) chatInput.value = '';
                setChatStatus('Message sent.');
            }
        });
        container.scrollTop = container.scrollHeight;
    }

    async function loadThread({ quiet = false } = {}) {
        if (!state.selectedPeer || state.threadLoading) return;
        state.threadLoading = true;
        const loading = $('[data-social-state="chat-loading"]');
        const error = $('[data-social-state="chat-error"]');
        const selected = $('[data-social-selected]');
        if (loading) loading.hidden = false;
        if (error) error.hidden = true;
        if (selected) selected.hidden = false;
        if (chatInput) chatInput.disabled = true;
        const sendButton = chatForm?.querySelector('button[type="submit"]');
        if (sendButton) sendButton.disabled = true;
        try {
            const result = await api('/wallet/social/messages', {
                peerPublicId: state.selectedPeer.publicId
            });
            state.selectedPeer = result.peer;
            if (chatInput) chatInput.disabled = false;
            const sendButton = chatForm?.querySelector('button[type="submit"]');
            if (sendButton) sendButton.disabled = false;
            const title = $('[data-social-peer-name]');
            if (title) title.textContent = state.selectedPeer.displayName;
            setAvatar($('[data-social-peer-avatar]'), state.selectedPeer.displayName);
            const friend = state.friends.find(item => item.publicId === state.selectedPeer.publicId);
            if (friend) friend.unreadCount = 0;
            renderPeople();
            renderMessages(Array.isArray(result.messages) ? result.messages : []);
            if (!quiet) setChatStatus('');
        } catch (errorValue) {
            if (error) error.hidden = false;
            if (loading) loading.hidden = true;
            if (selected) selected.hidden = true;
            if (chatInput) chatInput.disabled = true;
            if (sendButton) sendButton.disabled = true;
            setChatStatus(errorValue.message, true);
        } finally {
            state.threadLoading = false;
            if (loading) loading.hidden = true;
        }
    }

    async function sendFriendRequest(button) {
        const row = button.closest('[data-social-result]');
        const targetPublicId = row?.dataset.publicId || button.dataset.publicId;
        if (!targetPublicId) return;
        const note = row?.querySelector('[data-social-initial-message]')?.value.trim() || '';
        button.disabled = true;
        button.textContent = 'Sending…';
        try {
            await api('/wallet/social/requests', {
                targetPublicId,
                initialMessage: note,
                clientRequestId: makeId()
            });
            button.textContent = 'Sent';
            setSearchHint('Request sent. They can accept it before either of you can start a private chat.');
            await refreshState({ quiet: true, skipThread: true });
        } catch (error) {
            button.disabled = false;
            button.textContent = 'Connect';
            setSearchHint(error.message, true);
        }
    }

    async function respondToRequest(button, accept) {
        const row = button.closest('[data-social-request]');
        const requestId = row?.dataset.requestId;
        if (!requestId) return;
        const peer = {
            publicId: row.dataset.publicId,
            displayName: row.querySelector('[data-social-display-name]')?.textContent || 'Runner'
        };
        row.querySelectorAll('button').forEach(item => { item.disabled = true; });
        try {
            await api('/wallet/social/requests/respond', { requestId, accept });
            if (accept) {
                await refreshState({ quiet: true, skipThread: true });
                const friend = state.friends.find(item => item.publicId === peer.publicId) || peer;
                selectConversation(friend);
            } else {
                await refreshState({ quiet: true });
            }
        } catch (error) {
            row.querySelectorAll('button').forEach(item => { item.disabled = false; });
            setSearchHint(error.message, true);
        }
    }

    function makeId() {
        if (window.crypto?.randomUUID) return window.crypto.randomUUID();
        return `zrl-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    }

    async function sendMessage(event) {
        event.preventDefault();
        if (!state.selectedPeer || !chatInput) return;
        const content = chatInput.value.trim();
        if (!content) return;
        if (content.length > MESSAGE_LIMIT) {
            setChatStatus(`Messages can be up to ${MESSAGE_LIMIT} characters.`, true);
            return;
        }
        if (state.pendingSend && state.pendingSend.content !== content) {
            setChatStatus('Refresh this conversation to confirm the previous message before changing it.', true);
            return;
        }
        if (!state.pendingSend) {
            state.pendingSend = { content, clientMessageId: makeId() };
        }
        const button = chatForm.querySelector('button[type="submit"]');
        if (button) button.disabled = true;
        chatInput.disabled = true;
        setChatStatus('Sending…');
        try {
            const result = await api('/wallet/social/messages/send', {
                peerPublicId: state.selectedPeer.publicId,
                content,
                clientMessageId: state.pendingSend.clientMessageId
            });
            state.pendingSend = null;
            chatInput.value = '';
            setChatStatus('Message sent.');
            await loadThread({ quiet: true });
            await refreshState({ quiet: true, skipThread: true });
            if (!result.success) throw new Error('The message could not be confirmed.');
        } catch (error) {
            chatInput.disabled = false;
            setChatStatus(`Could not confirm whether this message was sent. Refresh the conversation before sending it again. ${error.message}`, true);
        } finally {
            if (button) button.disabled = false;
            if (!state.pendingSend) chatInput.disabled = false;
        }
    }

    function handleAction(button) {
        const action = button.dataset.socialAction;
        if (action === 'refresh') {
            refreshState();
        } else if (action === 'retry-search') {
            searchPlayers(state.searchQuery);
        } else if (action === 'send-request') {
            sendFriendRequest(button);
        } else if (action === 'accept-request') {
            respondToRequest(button, true);
        } else if (action === 'decline-request') {
            respondToRequest(button, false);
        } else if (action === 'open-chat') {
            const row = button.closest('[data-social-friend], [data-social-chat]');
            const publicId = row?.dataset.publicId || button.dataset.publicId;
            const peer = state.friends.find(friend => friend.publicId === publicId);
            if (peer) selectConversation(peer);
        } else if (action === 'retry-chat') {
            loadThread();
        }
    }

    screen.addEventListener('click', event => {
        const tab = event.target.closest('[data-social-tab]');
        if (tab) {
            setTab(tab.dataset.socialTab);
            return;
        }
        const action = event.target.closest('[data-social-action]');
        if (action) handleAction(action);
    });

    screen.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        const current = event.target.closest('[data-social-tab]');
        if (!current) return;
        event.preventDefault();
        const index = tabButtons.indexOf(current);
        const nextIndex = event.key === 'Home'
            ? 0
            : event.key === 'End'
                ? tabButtons.length - 1
                : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabButtons.length) % tabButtons.length;
        const next = tabButtons[nextIndex];
        next?.focus();
        if (next) setTab(next.dataset.socialTab);
    });

    searchForm?.addEventListener('submit', event => {
        event.preventDefault();
        searchPlayers();
    });

    chatForm?.addEventListener('submit', sendMessage);

    const openSocial = () => window.setTimeout(() => refreshState(), 0);
    document.getElementById('nav-chat')?.addEventListener('click', openSocial);
    document.getElementById('open-community-feed')?.addEventListener('click', openSocial);
    window.setInterval(() => {
        if (document.body.classList.contains('is-community-open') && state.loaded) {
            refreshState({ quiet: true });
        }
    }, 15000);

    screen.addEventListener('social:refresh', () => refreshState());
})();
