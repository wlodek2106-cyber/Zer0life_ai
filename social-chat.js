(() => {
    'use strict';

    const API_BASE = 'https://zer0life-ai-iz5n.onrender.com';
    const MESSAGE_LIMIT = 1000;
    const screen = document.getElementById('community-screen');
    if (!screen) return;

    const state = {
        myProfile: null,
        friends: [],
        incoming: [],
        outgoing: [],
        selectedPeer: null,
        searchResults: [],
        searchQuery: '',
        loaded: false,
        stateLoading: false,
        threadLoading: false,
        pendingSend: null,
        profileId: '',
        profile: null,
        profilePosts: [],
        profileCursor: null,
        profileView: 'posts',
        profileBusy: false,
        followersDirection: 'followers',
        followersCursor: null,
        followers: []
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
    const profileView = $('#social-profile-view');
    const profileContent = $('#social-profile-content');
    const profilePostsNode = $('#social-profile-posts');
    const profileStatus = $('#social-profile-status');
    const followersSheet = $('#social-followers-sheet');

    function node(tag, className, text) {
        const el = document.createElement(tag);
        if (className) el.className = className;
        if (text !== undefined) el.textContent = text;
        return el;
    }

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
        state.activeTab = name;
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
            const copy = row.querySelector('.social-person-copy');
            const followerCount = Number(player.followerCount) || 0;
            const stats = node('small', 'social-result-followers', `${followerCount.toLocaleString()} followers`);
            copy.appendChild(stats);
            const profileButton = node('button', 'social-profile-link', 'Profile');
            profileButton.type = 'button';
            profileButton.dataset.profilePublicId = player.publicId;
            row.appendChild(profileButton);
            const followButton = node('button', 'social-action-button social-result-follow', player.isFollowing ? 'Following' : 'Follow');
            followButton.type = 'button';
            followButton.dataset.profileFollow = player.publicId;
            followButton.dataset.following = String(Boolean(player.isFollowing));
            row.appendChild(followButton);
            row.querySelector('[data-social-action="send-request"]').dataset.publicId = player.publicId;
            container.appendChild(row);
        });
    }

    function setProfileMode(open) {
        profileView.hidden = !open;
        $('.social-tabs').hidden = open;
        tabPanels.forEach(panel => { panel.hidden = open || panel.dataset.socialPanel !== (state.activeTab || 'feed'); });
    }

    function renderProfileHeader() {
        const profile = state.profile;
        if (!profile) return;
        profileContent.replaceChildren();
        profileContent.appendChild(node('div', 'social-profile-cover'));
        const identity = node('div', 'social-profile-identity');
        identity.appendChild(node('span', 'social-profile-avatar', avatarInitial(profile.displayName)));
        const copy = node('div', 'social-profile-identity-copy');
        copy.append(node('h2', '', profile.displayName || 'Runner'), node('p', '', profile.tag || `#${String(profile.publicId).slice(0, 5).toUpperCase()}`));
        identity.appendChild(copy);
        if (!profile.isMe) {
            const follow = node('button', `social-profile-follow${profile.isFollowing ? ' is-following' : ''}`, profile.isFollowing ? 'Following' : 'Follow');
            follow.type = 'button';
            follow.dataset.profileAction = 'follow';
            follow.disabled = state.profileBusy;
            follow.setAttribute('aria-pressed', String(Boolean(profile.isFollowing)));
            identity.appendChild(follow);
        }
        profileContent.appendChild(identity);
        const stats = node('div', 'social-profile-stats');
        [['followers', profile.followerCount], ['following', profile.followingCount], ['posts', profile.postCount]].forEach(([kind, amount]) => {
            const button = node('button', 'social-profile-stat');
            button.type = 'button';
            button.dataset.profileAction = kind === 'followers' ? 'followers' : kind === 'following' ? 'following' : 'view-posts';
            const strong = node('strong', '', (Number(amount) || 0).toLocaleString());
            button.append(strong, document.createTextNode(` ${kind}`));
            stats.appendChild(button);
        });
        profileContent.appendChild(stats);
        const date = profile.joinedAt ? new Date(profile.joinedAt) : null;
        const joined = date && !Number.isNaN(date.getTime()) ? `RUNNER SINCE ${date.toLocaleDateString(undefined, { month: 'short', year: 'numeric' }).toUpperCase()}` : 'ZER0LIFE RUNNER';
        profileContent.appendChild(node('p', 'social-profile-meta', joined));
        const views = node('div', 'social-profile-views');
        [['posts', 'Posts'], ['replies', 'Replies'], ['reposts', 'Reposts'], ['media', 'Media']].forEach(([value, label]) => {
            const button = node('button', state.profileView === value ? 'is-active' : '', label);
            button.type = 'button';
            button.dataset.profileView = value;
            button.setAttribute('aria-pressed', String(state.profileView === value));
            views.appendChild(button);
        });
        profileContent.appendChild(views);
    }

    function renderProfilePosts() {
        profilePostsNode.replaceChildren();
        if (!state.profilePosts.length) {
            profilePostsNode.appendChild(node('div', 'social-profile-state', 'No activity here yet. When this runner shares a moment, it will show up here.'));
            return;
        }
        state.profilePosts.forEach(post => {
            const card = node('article', 'community-post');
            card.dataset.profilePostId = String(post.id);
            const head = node('div', 'community-post-head');
            head.append(node('span', 'community-avatar', avatarInitial(post.author)), node('div', 'community-post-author', ''));
            const authorCopy = head.lastChild;
            authorCopy.append(node('strong', '', post.author || 'Runner'), node('time', '', timeLabel(post.createdAt)));
            card.appendChild(head);
            if (post.timelineType && post.timelineType !== 'post') {
                card.appendChild(node('p', 'community-post-context', `${post.timelineType === 'repost' ? 'Reposted' : post.timelineType === 'reply' ? 'Reply' : 'Shared media'} · ${timeLabel(post.repostedAt || post.createdAt)}`));
            }
            if (post.content) card.appendChild(node('p', 'community-post-content', post.content));
            if (post.reply?.content) card.appendChild(node('p', 'community-post-content social-profile-reply', post.reply.content));
            if (post.mediaUrl) {
                try {
                    const media = new URL(post.mediaUrl, API_BASE);
                    if (['https:', 'http:'].includes(media.protocol)) {
                        const image = node('img', 'community-post-media');
                        image.src = media.href;
                        image.alt = `Image shared by ${post.author || 'a runner'}`;
                        image.loading = 'lazy';
                        image.addEventListener('error', () => image.remove(), { once: true });
                        card.appendChild(image);
                    }
                } catch {}
            }
            const actions = node('div', 'community-post-actions');
            const like = node('button', `community-post-action${post.likedByMe ? ' is-liked' : ''}`, `♥ ${Number(post.likeCount) || 0}`);
            like.type = 'button'; like.dataset.profileAction = 'like'; like.setAttribute('aria-pressed', String(Boolean(post.likedByMe)));
            const reply = node('button', 'community-post-action', `Reply ${Number(post.commentCount) || 0}`);
            reply.type = 'button'; reply.dataset.profileAction = 'reply';
            const repost = node('button', `community-post-action${post.repostedByMe ? ' is-reposted' : ''}`, `↻ ${Number(post.repostCount) || 0}`);
            repost.type = 'button'; repost.dataset.profileAction = 'repost'; repost.setAttribute('aria-pressed', String(Boolean(post.repostedByMe)));
            repost.disabled = Boolean(post.isMine);
            actions.append(like, reply, repost);
            card.appendChild(actions);
            const comments = node('div', 'community-comment-list');
            (Array.isArray(post.comments) ? post.comments : []).forEach(comment => {
                const line = node('div', 'community-comment');
                line.append(node('strong', '', comment.author || 'Runner'), document.createTextNode(` ${comment.content || ''}`));
                comments.appendChild(line);
            });
            if (comments.childNodes.length) card.appendChild(comments);
            const form = node('form', 'community-comment-form');
            form.dataset.profileReplyId = String(post.id);
            const input = node('textarea', 'community-comment-input');
            input.name = 'content'; input.rows = 1; input.maxLength = 500; input.placeholder = 'Write a reply'; input.setAttribute('aria-label', 'Write a reply');
            const send = node('button', 'community-comment-submit', 'Send'); send.type = 'submit';
            form.append(input, send);
            card.appendChild(form);
            profilePostsNode.appendChild(card);
        });
    }

    async function loadProfile(publicId, { append = false } = {}) {
        if (!publicId || state.profileBusy) return;
        state.profileBusy = true;
        profileStatus.textContent = append ? 'Loading more activity…' : 'Loading runner profile…';
        profileStatus.className = 'social-profile-state';
        if (!append) {
            state.profileId = publicId;
            state.profilePosts = [];
            state.profileCursor = null;
            profilePostsNode.replaceChildren(node('div', 'community-skeleton-card'));
            setProfileMode(true);
        }
        try {
            const result = await api('/wallet/community/profile', {
                publicId,
                view: state.profileView,
                ...(append && state.profileCursor ? { cursor: state.profileCursor } : {})
            });
            state.profile = result.profile;
            const received = Array.isArray(result.posts) ? result.posts : [];
            state.profilePosts = append ? [...state.profilePosts, ...received] : received;
            state.profileCursor = result.nextCursor ?? null;
            renderProfileHeader();
            renderProfilePosts();
            $('#social-profile-more').hidden = state.profileCursor === null || state.profileCursor === undefined;
            profileStatus.textContent = '';
        } catch (error) {
            profileStatus.className = 'social-profile-state social-error-card';
            profileStatus.textContent = error.message || 'Could not load this runner profile.';
            if (!append) profilePostsNode.replaceChildren();
        } finally {
            state.profileBusy = false;
        }
    }

    async function toggleFollow(publicId, follow, button) {
        if (!publicId || button?.disabled) return;
        if (button) button.disabled = true;
        try {
            const result = await api('/wallet/community/follow', { targetPublicId: publicId, follow });
            const isFollowing = Boolean(result.isFollowing);
            if (state.profile?.publicId === publicId) {
                state.profile.isFollowing = isFollowing;
                state.profile.followerCount = Number(result.followerCount) || 0;
                renderProfileHeader();
            }
            state.searchResults = state.searchResults.map(player => player.publicId === publicId
                ? { ...player, isFollowing, followerCount: Number(result.followerCount) || player.followerCount } : player);
            renderSearchResults(state.searchResults);
            document.querySelectorAll(`[data-profile-follow="${CSS.escape(publicId)}"]`).forEach(item => {
                item.dataset.following = String(isFollowing);
                item.textContent = isFollowing ? 'Following' : 'Follow';
            });
        } catch (error) {
            if (state.profile?.publicId === publicId) profileStatus.textContent = error.message;
            else setSearchHint(error.message, true);
        } finally {
            if (button?.isConnected) button.disabled = false;
        }
    }

    async function showFollowers(direction) {
        if (!state.profile?.publicId) return;
        state.followersDirection = direction;
        state.followersCursor = null;
        state.followers = [];
        $('#social-followers-title').textContent = direction === 'followers' ? 'Followers' : 'Following';
        const list = $('#social-followers-list');
        list.replaceChildren(node('div', 'social-profile-state', 'Loading runners…'));
        followersSheet.hidden = false;
        try {
            const result = await api('/wallet/community/follows/list', { publicId: state.profile.publicId, direction });
            state.followers = Array.isArray(result.players) ? result.players : [];
            state.followersCursor = result.nextCursor ?? null;
            renderFollowers();
        } catch (error) {
            list.replaceChildren(node('div', 'social-profile-state social-error-card', error.message || 'Could not load this list.'));
        }
    }

    function renderFollowers() {
        const list = $('#social-followers-list');
        list.replaceChildren();
        if (!state.followers.length) list.appendChild(node('div', 'social-profile-state', 'No runners to show yet.'));
        state.followers.forEach(player => {
            const row = node('div', 'social-followers-person');
            row.appendChild(node('span', 'social-avatar', avatarInitial(player.displayName)));
            const open = node('button', '', '');
            open.type = 'button'; open.dataset.profilePublicId = player.publicId;
            open.append(node('strong', '', player.displayName || 'Runner'), node('small', '', player.tag || 'Zer0Life runner'));
            row.appendChild(open);
            const follow = node('button', 'social-action-button', player.isFollowing ? 'Following' : 'Follow');
            follow.type = 'button'; follow.dataset.profileFollow = player.publicId; follow.dataset.following = String(Boolean(player.isFollowing));
            row.appendChild(follow);
            list.appendChild(row);
        });
        if (state.followersCursor) {
            const more = node('button', 'community-load-more', 'Load more runners');
            more.type = 'button'; more.dataset.profileAction = 'more-followers';
            list.appendChild(more);
        }
    }

    async function loadMoreFollowers() {
        if (!state.followersCursor) return;
        try {
            const result = await api('/wallet/community/follows/list', {
                publicId: state.profile.publicId,
                direction: state.followersDirection,
                cursor: state.followersCursor
            });
            state.followers = [...state.followers, ...(Array.isArray(result.players) ? result.players : [])];
            state.followersCursor = result.nextCursor ?? null;
            renderFollowers();
        } catch (error) {
            $('#social-followers-list').appendChild(node('div', 'social-profile-state social-error-card', error.message || 'Could not load more runners.'));
        }
    }

    async function profilePostAction(button) {
        const card = button.closest('[data-profile-post-id]');
        const post = state.profilePosts.find(item => String(item.id) === card?.dataset.profilePostId);
        if (!post) return;
        button.disabled = true;
        try {
            if (button.dataset.profileAction === 'like') {
                const result = await api('/wallet/community/posts/like', { postId: post.id });
                post.likedByMe = Boolean(result.likedByMe); post.likeCount = Number(result.likeCount) || 0;
            } else if (button.dataset.profileAction === 'repost') {
                const result = await api('/wallet/community/posts/repost', { postId: post.id });
                post.repostedByMe = Boolean(result.repostedByMe); post.repostCount = Number(result.repostCount) || 0;
            } else if (button.dataset.profileAction === 'reply') {
                card.querySelector('.community-comment-input')?.focus();
            }
        } catch (error) {
            profileStatus.textContent = error.message || 'Could not update this post.';
        } finally {
            button.disabled = false;
            if (button.dataset.profileAction !== 'reply') renderProfilePosts();
        }
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
            state.myProfile = result.profile;
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
        const profileLink = event.target.closest('[data-profile-public-id]');
        if (profileLink) {
            followersSheet.hidden = true;
            loadProfile(profileLink.dataset.profilePublicId);
            return;
        }
        const followControl = event.target.closest('[data-profile-follow]');
        if (followControl) {
            toggleFollow(followControl.dataset.profileFollow, followControl.dataset.following !== 'true', followControl);
            return;
        }
        const profileControl = event.target.closest('[data-profile-action]');
        if (profileControl) {
            const action = profileControl.dataset.profileAction;
            if (action === 'back') {
                followersSheet.hidden = true;
                setProfileMode(false);
                profilePostsNode.replaceChildren();
            } else if (action === 'follow') {
                toggleFollow(state.profile?.publicId, !state.profile?.isFollowing, profileControl);
            } else if (action === 'my-profile') {
                if (state.myProfile?.publicId) loadProfile(state.myProfile.publicId);
                else refreshState().then(() => state.myProfile?.publicId && loadProfile(state.myProfile.publicId));
            } else if (action === 'followers' || action === 'following') {
                showFollowers(action);
            } else if (action === 'more') {
                loadProfile(state.profileId, { append: true });
            } else if (action === 'close-followers') {
                followersSheet.hidden = true;
            } else if (action === 'more-followers') {
                loadMoreFollowers();
            } else if (action === 'view-posts') {
                state.profileView = 'posts';
                loadProfile(state.profileId);
            } else if (action === 'like' || action === 'repost' || action === 'reply') {
                profilePostAction(profileControl);
            }
            return;
        }
        const viewButton = event.target.closest('[data-profile-view]');
        if (viewButton) {
            state.profileView = viewButton.dataset.profileView;
            loadProfile(state.profileId);
            return;
        }
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

    profilePostsNode?.addEventListener('submit', async event => {
        const form = event.target.closest('[data-profile-reply-id]');
        if (!form) return;
        event.preventDefault();
        const input = form.elements.content;
        const content = String(input.value || '').trim();
        if (!content) return;
        const post = state.profilePosts.find(item => String(item.id) === form.dataset.profileReplyId);
        if (!post) return;
        const button = form.querySelector('button[type="submit"]');
        button.disabled = true;
        try {
            const result = await api('/wallet/community/comments', { postId: post.id, displayName: displayName(), content });
            post.comments = [...(Array.isArray(post.comments) ? post.comments : []), result.comment];
            post.commentCount = Number(result.commentCount) || (Number(post.commentCount) || 0) + 1;
            input.value = '';
            renderProfilePosts();
        } catch (error) {
            profileStatus.textContent = error.message || 'Reply could not be sent.';
        } finally {
            if (button.isConnected) button.disabled = false;
        }
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
    screen.addEventListener('social:open-profile', event => loadProfile(event.detail?.publicId));
})();
