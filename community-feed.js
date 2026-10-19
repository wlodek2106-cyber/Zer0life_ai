(() => {
    'use strict';

    const API_BASE = 'https://zer0life-ai-iz5n.onrender.com';
    const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
    const MAX_POST_CHARS = 1000;
    const MAX_COMMENT_CHARS = 500;
    const FEED_RETRY_DELAYS = [1500, 4000, 8000];
    const screen = document.getElementById('community-screen');
    if (!screen) return;

    const list = document.getElementById('community-feed-list');
    const status = document.getElementById('community-feed-status');
    const loadMoreButton = document.getElementById('community-load-more');
    const refreshButton = screen.querySelector('[data-community-action="refresh"]');
    const postInput = document.getElementById('community-post-text');
    const postCounter = document.getElementById('community-post-counter');
    const postSubmit = screen.querySelector('[data-community-action="submit-post"]');
    const composerMessage = document.getElementById('community-composer-message');
    const imageInput = document.getElementById('community-image-input');
    const attachButton = screen.querySelector('[data-community-action="pick-image"]');
    const imagePreview = document.getElementById('community-image-preview');
    const previewImage = document.getElementById('community-image-preview-img');
    const imageName = document.getElementById('community-image-name');
    const displayNameNode = document.getElementById('community-display-name');
    const selfAvatar = document.getElementById('community-self-avatar');
    const pendingBalanceNode = document.getElementById('zeronet-pending-balance');
    const claimButton = document.getElementById('zeronet-claim');
    const rewardsStatus = document.getElementById('zeronet-rewards-status');

    const state = {
        posts: [],
        nextCursor: null,
        isModerator: false,
        open: false,
        loading: false,
        loadingMore: false,
        sending: false,
        timer: null,
        imageData: '',
        imageFileName: '',
        commentBusy: new Set(),
        likeBusy: new Set(),
        deleteBusy: new Set(),
        expanded: new Set(),
        scope: 'latest',
        pendingZrl: 0,
        viewerAvatarUrl: '',
        claiming: false
    };

    const icons = {
        heart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.8 8.7c0 4.1-8.8 10-8.8 10s-8.8-5.9-8.8-10A4.6 4.6 0 0 1 12 6.6a4.6 4.6 0 0 1 8.8 2.1Z"/></svg>',
        comment: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H6l-3 2v-9.5A7.5 7.5 0 0 1 10.5 4h2A7.5 7.5 0 0 1 20 11.5Z"/><path d="M8 11h8M8 14h5"/></svg>',
        send: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6"/></svg>',
        empty: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v12H9l-5 3V5Z"/><path d="M8 9h8M8 13h5"/></svg>'
    };

    function displayName() {
        try {
            const saved = typeof username !== 'undefined' ? username : '';
            return String(saved || '').trim().slice(0, 64) || 'Runner';
        } catch {
            return 'Runner';
        }
    }

    function initData() {
        return String(window.Telegram?.WebApp?.initData || '');
    }

    function setDisplayName() {
        const name = displayName();
        displayNameNode.textContent = name;
        setAvatarImage(selfAvatar, name, state.viewerAvatarUrl);
    }

    async function api(path, payload, { retryRead = false } = {}) {
        const signedData = initData();
        if (!signedData) throw new Error('Open Zer0Life Run from Telegram to use Zer0Net.');
        const body = JSON.stringify({ initData: signedData, ...payload });
        let response;
        for (let attempt = 0; ; attempt += 1) {
            try {
                response = await fetch(API_BASE + path, {
                    method: 'POST',
                    mode: 'cors',
                    credentials: 'omit',
                    cache: 'no-store',
                    headers: { 'Content-Type': 'application/json' },
                    body
                });
            } catch {
                if (retryRead && attempt < FEED_RETRY_DELAYS.length) {
                    await new Promise(resolve => window.setTimeout(resolve, FEED_RETRY_DELAYS[attempt]));
                    continue;
                }
                if (path === '/wallet/community/posts') {
                    throw new Error('Could not confirm whether your post was saved. Refresh the feed before sending it again.');
                }
                throw new Error('Could not reach the Zer0Net server from this Telegram session. Check your connection and try again.');
            }

            if (retryRead && [502, 503, 504].includes(response.status) && attempt < FEED_RETRY_DELAYS.length) {
                await new Promise(resolve => window.setTimeout(resolve, FEED_RETRY_DELAYS[attempt]));
                continue;
            }
            break;
        }
        let result;
        try {
            result = await response.json();
        } catch {
            throw new Error('Zer0Net returned an unreadable response.');
        }
        if (!response.ok || result?.success === false) {
            throw new Error(result?.message || result?.error || 'Could not complete that request. Try again.');
        }
        return result;
    }

    function make(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function safeMediaUrl(value) {
        if (typeof value !== 'string' || !value.trim()) return '';
        try {
            const url = new URL(value, API_BASE);
            if (url.protocol !== 'https:' && url.protocol !== 'http:') return '';
            return url.href;
        } catch {
            return '';
        }
    }

    function initials(author) {
        const name = String(author || 'Runner').trim();
        return Array.from(name)[0]?.toUpperCase() || 'R';
    }

    function setAvatarImage(avatar, author, imageUrl) {
        if (!avatar) return;
        const initial = initials(author);
        const url = safeMediaUrl(imageUrl);
        avatar.classList.remove('has-image');
        if (!url) {
            avatar.textContent = initial;
            return;
        }
        const image = make('img', '');
        image.src = url;
        image.alt = '';
        image.loading = 'lazy';
        image.referrerPolicy = 'no-referrer';
        image.addEventListener('error', () => {
            avatar.classList.remove('has-image');
            avatar.textContent = initial;
        }, { once: true });
        avatar.replaceChildren(image);
        avatar.classList.add('has-image');
    }

    function updateRewards(pendingZrl = state.pendingZrl) {
        if (Number.isFinite(Number(pendingZrl))) state.pendingZrl = Math.max(0, Number(pendingZrl));
        if (pendingBalanceNode) {
            pendingBalanceNode.textContent = state.pendingZrl.toLocaleString('en-US', {
                maximumFractionDigits: 6
            });
        }
        if (claimButton) {
            claimButton.disabled = state.claiming || state.pendingZrl <= 0;
            claimButton.textContent = state.claiming ? 'Claiming…' : 'Claim';
        }
    }

    function timeLabel(value) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return 'Recently';
        const age = Math.max(0, Date.now() - date.getTime());
        if (age < 60_000) return 'Just now';
        if (age < 3_600_000) return `${Math.floor(age / 60_000)}m ago`;
        if (age < 86_400_000) return `${Math.floor(age / 3_600_000)}h ago`;
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }

    function setComposerMessage(message, isError = false) {
        composerMessage.textContent = message || '';
        composerMessage.classList.toggle('is-error', Boolean(message && isError));
    }

    function updateComposer() {
        const length = postInput.value.length;
        postCounter.textContent = `${length.toLocaleString()} / 1,000`;
        postSubmit.disabled = state.sending || (!postInput.value.trim() && !state.imageData);
        postSubmit.querySelector('span').textContent = state.sending ? 'Sending…' : 'Post';
        postInput.disabled = state.sending;
        attachButton.disabled = state.sending;
        const removeImageButton = imagePreview.querySelector('button');
        if (removeImageButton) removeImageButton.disabled = state.sending;
    }

    function skeletons() {
        const wrap = make('div', 'community-skeleton');
        for (let i = 0; i < 3; i += 1) wrap.appendChild(make('div', 'community-skeleton-card'));
        list.replaceChildren(wrap);
    }

    function showEmpty() {
        const empty = make('div', 'community-empty');
        const mark = make('div', 'community-empty-mark');
        mark.innerHTML = icons.empty;
        empty.append(mark, make('strong', '', 'Your pack is just getting started.'));
        empty.appendChild(make('p', '', 'Be the first to share a route, a small win, or the run that changed your day.'));
        list.replaceChildren(empty);
    }

    function renderComment(comment) {
        const row = make('div', 'community-comment');
        const name = make('strong', '', String(comment?.author || 'Runner'));
        const content = document.createTextNode(` ${String(comment?.content || '')}`);
        const time = make('time', '', timeLabel(comment?.createdAt));
        row.append(name, content, time);
        return row;
    }

    function renderPost(post) {
        const card = make('article', 'community-post');
        card.dataset.postId = String(post.id);

        const head = make('div', 'community-post-head');
        const avatar = make('span', 'community-avatar', initials(post.author));
        setAvatarImage(avatar, post.author, post.authorAvatarUrl);
        avatar.setAttribute('aria-hidden', 'true');
        const author = make('div', 'community-post-author');
        const authorButton = make('button', '', String(post.author || 'Runner'));
        authorButton.type = 'button';
        authorButton.dataset.openProfile = String(post.authorPublicId || '');
        author.append(authorButton);
        const time = make('time', '', timeLabel(post.createdAt));
        author.appendChild(time);
        const canDelete = state.isModerator || Boolean(post.isMine);
        head.append(avatar, author);
        if (canDelete) {
            const del = make('button', 'community-post-delete', state.deleteBusy.has(String(post.id)) ? 'Removing…' : 'Delete');
            del.type = 'button';
            del.dataset.communityAction = 'delete-post';
            del.disabled = state.deleteBusy.has(String(post.id));
            head.appendChild(del);
        }
        card.appendChild(head);
        if (post.timelineType === 'repost' && post.reposter) {
            const context = make('p', 'community-post-context', '');
            context.append(make('strong', '', String(post.reposter)), document.createTextNode(' reposted this'));
            card.insertBefore(context, head);
        }

        if (post.content) card.appendChild(make('p', 'community-post-content', String(post.content)));
        const mediaUrl = safeMediaUrl(post.mediaUrl);
        if (mediaUrl) {
            const image = make('img', 'community-post-media');
            image.src = mediaUrl;
            image.alt = `Image shared by ${String(post.author || 'a runner')}`;
            image.loading = 'lazy';
            image.referrerPolicy = 'no-referrer';
            image.addEventListener('error', () => image.remove(), { once: true });
            card.appendChild(image);
        }

        const actions = make('div', 'community-post-actions');
        const like = make('button', `community-post-action${post.likedByMe ? ' is-liked' : ''}`);
        like.type = 'button';
        like.dataset.communityAction = 'like';
        like.disabled = state.likeBusy.has(String(post.id));
        like.setAttribute('aria-pressed', String(Boolean(post.likedByMe)));
        like.setAttribute('aria-label', post.likedByMe ? 'Unlike post' : 'Like post');
        like.innerHTML = icons.heart;
        like.appendChild(make('span', '', String(Number(post.likeCount) || 0)));
        actions.appendChild(like);

        const commentsToggle = make('button', 'community-post-action');
        commentsToggle.type = 'button';
        commentsToggle.dataset.communityAction = 'toggle-comments';
        commentsToggle.setAttribute('aria-expanded', String(state.expanded.has(String(post.id))));
        commentsToggle.innerHTML = icons.comment;
        commentsToggle.appendChild(make('span', '', `${Number(post.commentCount) || 0} ${Number(post.commentCount) === 1 ? 'reply' : 'replies'}`));
        actions.appendChild(commentsToggle);
        const repost = make('button', `community-post-action${post.repostedByMe ? ' is-reposted' : ''}`);
        repost.type = 'button';
        repost.dataset.communityAction = 'repost';
        repost.disabled = Boolean(post._repostBusy || post.isMine);
        repost.setAttribute('aria-pressed', String(Boolean(post.repostedByMe)));
        repost.setAttribute('aria-label', post.repostedByMe ? 'Remove repost' : 'Repost');
        repost.textContent = `↻ ${Number(post.repostCount) || 0}`;
        actions.appendChild(repost);
        card.appendChild(actions);
        if (post._actionError) card.appendChild(make('p', 'community-composer-message is-error', post._actionError));

        if (state.expanded.has(String(post.id))) {
            const commentList = make('div', 'community-comment-list');
            (Array.isArray(post.comments) ? post.comments : []).forEach(comment => commentList.appendChild(renderComment(comment)));
            card.appendChild(commentList);

            const form = make('form', 'community-comment-form');
            form.dataset.postId = String(post.id);
            const input = make('textarea', 'community-comment-input');
            input.name = 'content';
            input.rows = 1;
            input.maxLength = MAX_COMMENT_CHARS;
            input.placeholder = 'Add a thought…';
            input.value = String(post._commentDraft || '');
            input.setAttribute('aria-label', 'Write a comment, up to 500 characters');
            const submit = make('button', 'community-comment-submit');
            submit.type = 'submit';
            submit.disabled = state.commentBusy.has(String(post.id));
            submit.setAttribute('aria-label', 'Send comment');
            submit.innerHTML = icons.send;
            form.append(input, submit);
            card.appendChild(form);
            if (post._commentError) card.appendChild(make('p', 'community-composer-message is-error', post._commentError));
        }
        return card;
    }

    function renderFeed() {
        status.replaceChildren();
        if (state.loading && !state.posts.length) {
            skeletons();
            return;
        }
        if (!state.posts.length) {
            showEmpty();
            return;
        }
        const fragment = document.createDocumentFragment();
        state.posts.forEach(post => fragment.appendChild(renderPost(post)));
        list.replaceChildren(fragment);
        loadMoreButton.hidden = state.nextCursor === null || state.nextCursor === undefined;
        loadMoreButton.disabled = state.loadingMore;
        loadMoreButton.textContent = state.loadingMore ? 'Loading older posts…' : 'Load older posts ↓';
    }

    function setFeedError(error, hasPosts = false) {
        status.replaceChildren();
        const message = make('div', '', error?.message || 'Something interrupted the feed.');
        status.appendChild(message);
        const retry = make('button', '', 'Try again');
        retry.type = 'button';
        retry.dataset.communityAction = 'refresh';
        status.appendChild(retry);
        if (!hasPosts) list.replaceChildren();
    }

    function mergeUnique(incoming, existing) {
        const seen = new Set();
        const combined = [];
        [...incoming, ...existing].forEach(post => {
            const key = String(post.id);
            if (!seen.has(key)) {
                seen.add(key);
                combined.push(post);
            }
        });
        return combined;
    }

    async function fetchFeed({ append = false, polling = false } = {}) {
        if (state.loading || state.loadingMore) return;
        if (append && (state.nextCursor === null || state.nextCursor === undefined)) return;
        if (append) state.loadingMore = true;
        else state.loading = true;
        if (!append && !polling) {
            status.replaceChildren();
            if (!state.posts.length) skeletons();
            refreshButton.classList.add('is-refreshing');
        }
        if (append) renderFeed();

        try {
            const payload = append ? { cursor: state.nextCursor } : {};
            const result = await api('/wallet/community/feed', { scope: state.scope, ...payload }, { retryRead: true });
            if (Object.prototype.hasOwnProperty.call(result, 'pendingZrl')) {
                updateRewards(result.pendingZrl);
            }
            if (Object.prototype.hasOwnProperty.call(result, 'viewerAvatarUrl')) {
                state.viewerAvatarUrl = result.viewerAvatarUrl || '';
                setDisplayName();
            }
            const received = Array.isArray(result.posts) ? result.posts : [];
            if (append) state.posts = mergeUnique(state.posts, received);
            else if (polling) state.posts = mergeUnique(received, state.posts);
            else state.posts = received;
            if (Object.prototype.hasOwnProperty.call(result, 'nextCursor')) {
                state.nextCursor = result.nextCursor;
            } else if (!append) {
                state.nextCursor = null;
            }
            state.isModerator = Boolean(result.isModerator);
            renderFeed();
        } catch (error) {
            if (polling && state.posts.length) {
                // A silent background refresh should not interrupt reading.
            } else setFeedError(error, state.posts.length > 0);
        } finally {
            state.loading = false;
            state.loadingMore = false;
            refreshButton.classList.remove('is-refreshing');
            loadMoreButton.disabled = false;
        }
    }

    function openFeed() {
        if (state.open) return;
        state.open = true;
        document.body.classList.add('is-community-open');
        screen.setAttribute('aria-hidden', 'false');
        setDisplayName();
        fetchFeed();
        if (state.timer) window.clearInterval(state.timer);
        state.timer = window.setInterval(() => {
            if (state.open && !document.hidden) fetchFeed({ polling: true });
        }, 30_000);
        const backButton = window.Telegram?.WebApp?.BackButton;
        if (backButton) {
            backButton.show();
            backButton.onClick(closeFeed);
        }
    }

    function closeFeed() {
        if (!state.open) return;
        state.open = false;
        document.body.classList.remove('is-community-open');
        screen.setAttribute('aria-hidden', 'true');
        if (state.timer) window.clearInterval(state.timer);
        state.timer = null;
        const backButton = window.Telegram?.WebApp?.BackButton;
        if (backButton) {
            backButton.offClick(closeFeed);
            backButton.hide();
        }
    }

    async function selectImage(file) {
        setComposerMessage('');
        if (!file) return;
        if (!['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type)) {
            setComposerMessage('Choose a JPEG, PNG, WebP, or GIF image to attach.', true);
            imageInput.value = '';
            return;
        }
        if (file.size > MAX_IMAGE_BYTES) {
            setComposerMessage('That image is over 2 MiB. Choose a smaller one.', true);
            imageInput.value = '';
            return;
        }
        try {
            const readDataUrl = blob => new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(String(reader.result || ''));
                reader.onerror = () => reject(new Error('Could not read that image. Try another file.'));
                reader.readAsDataURL(blob);
            });
            let dataUrl;
            if (file.type === 'image/gif') {
                dataUrl = await readDataUrl(file);
            } else {
                let image;
                let revokeUrl = '';
                if (window.createImageBitmap) {
                    try {
                        image = await window.createImageBitmap(file);
                    } catch {
                        image = null;
                    }
                }
                if (!image) {
                    revokeUrl = URL.createObjectURL(file);
                    image = await new Promise((resolve, reject) => {
                        const element = new Image();
                        element.onload = () => resolve(element);
                        element.onerror = () => reject(new Error('Could not open that image.'));
                        element.src = revokeUrl;
                    });
                }
                const maxEdge = 2560;
                const scale = Math.min(1, maxEdge / Math.max(image.width, image.height));
                const canvas = document.createElement('canvas');
                canvas.width = Math.max(1, Math.round(image.width * scale));
                canvas.height = Math.max(1, Math.round(image.height * scale));
                canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
                if (typeof image.close === 'function') image.close();
                if (revokeUrl) URL.revokeObjectURL(revokeUrl);
                const blob = await new Promise(resolve => canvas.toBlob(resolve, file.type, 0.9));
                if (!blob || !['image/jpeg', 'image/png', 'image/webp'].includes(blob.type)) {
                    throw new Error('That image format is not supported on this device.');
                }
                if (blob.size > MAX_IMAGE_BYTES) {
                    throw new Error('The processed image is over 2 MiB. Choose a smaller one.');
                }
                dataUrl = await readDataUrl(blob);
            }
            if (!/^data:image\/[a-z0-9.+-]+;base64,/i.test(dataUrl)) throw new Error('That image could not be attached.');
            state.imageData = dataUrl;
            state.imageFileName = file.name;
            previewImage.src = dataUrl;
            imageName.textContent = file.name || 'Image attached';
            imagePreview.hidden = false;
            updateComposer();
        } catch (error) {
            setComposerMessage(error.message || 'Could not read that image.', true);
        } finally {
            imageInput.value = '';
        }
    }

    function removeImage() {
        state.imageData = '';
        state.imageFileName = '';
        previewImage.removeAttribute('src');
        imagePreview.hidden = true;
        updateComposer();
    }

    async function submitPost() {
        if (state.sending) return;
        const content = postInput.value.trim();
        if (content.length > MAX_POST_CHARS) {
            setComposerMessage('Posts can be up to 1,000 characters.', true);
            return;
        }
        if (!content && !state.imageData) {
            setComposerMessage('Add a few words or an image before posting.', true);
            return;
        }
        state.sending = true;
        setComposerMessage('');
        updateComposer();
        try {
            const payload = { displayName: displayName(), content };
            if (state.imageData) payload.image = state.imageData;
            const result = await api('/wallet/community/posts', payload);
            if (!result.post) throw new Error('Your post was sent, but the feed could not display it.');
            if (Object.prototype.hasOwnProperty.call(result, 'pendingZrl')) {
                updateRewards(result.pendingZrl);
            }
            state.posts = mergeUnique([result.post], state.posts);
            state.expanded.delete(String(result.post.id));
            postInput.value = '';
            removeImage();
            setComposerMessage(`Posted. +${Number(result.rewardZrl) || 10} ZRL added to your Zer0Net balance.`);
            renderFeed();
            screen.querySelector('.community-feed-heading')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        } catch (error) {
            setComposerMessage(error.message || 'Your post could not be sent. Try again.', true);
        } finally {
            state.sending = false;
            updateComposer();
        }
    }

    async function claimRewards() {
        if (state.claiming || state.pendingZrl <= 0) return;
        state.claiming = true;
        if (rewardsStatus) {
            rewardsStatus.textContent = '';
            rewardsStatus.classList.remove('is-error');
        }
        updateRewards();
        try {
            const result = await api('/wallet/community/rewards/claim', {});
            updateRewards(result.pendingZrl);
            const claimed = Number(result.claimedZrl) || 0;
            if (window.secureWallet?.applyResult) {
                window.secureWallet.applyResult({
                    availableZrl: Number(result.gameBalanceZrl) || 0
                });
            }
            if (rewardsStatus) {
                rewardsStatus.textContent = claimed > 0
                    ? `Claimed ${claimed.toLocaleString('en-US', { maximumFractionDigits: 6 })} ZRL. Added to your game balance.`
                    : 'No new rewards to claim. Your game balance is up to date.';
                rewardsStatus.classList.remove('is-error');
            }
        } catch (error) {
            if (rewardsStatus) {
                rewardsStatus.textContent = `${error.message || 'Could not confirm the claim.'} Refresh Zer0Net before trying again. Claims cannot credit the same post twice.`;
                rewardsStatus.classList.add('is-error');
            }
        } finally {
            state.claiming = false;
            updateRewards();
        }
    }

    async function toggleLike(postId) {
        const key = String(postId);
        if (state.likeBusy.has(key)) return;
        const post = state.posts.find(item => String(item.id) === key);
        if (!post) return;
        state.likeBusy.add(key);
        post.likedByMe = !post.likedByMe;
        post.likeCount = Math.max(0, (Number(post.likeCount) || 0) + (post.likedByMe ? 1 : -1));
        renderFeed();
        try {
            const result = await api('/wallet/community/posts/like', { postId: post.id });
            post.likedByMe = Boolean(result.likedByMe);
            post.likeCount = Number(result.likeCount) || 0;
        } catch (error) {
            post.likedByMe = !post.likedByMe;
            post.likeCount = Math.max(0, (Number(post.likeCount) || 0) + (post.likedByMe ? 1 : -1));
            post._actionError = error.message || 'Like could not be updated.';
        } finally {
            state.likeBusy.delete(key);
            renderFeed();
        }
    }

    async function submitComment(form) {
        const postId = form.dataset.postId;
        const key = String(postId);
        if (state.commentBusy.has(key)) return;
        const input = form.elements.content;
        const content = String(input.value || '').trim();
        if (!content) return;
        if (content.length > MAX_COMMENT_CHARS) {
            input.setCustomValidity('Comments can be up to 500 characters.');
            input.reportValidity();
            input.setCustomValidity('');
            return;
        }
        const post = state.posts.find(item => String(item.id) === key);
        if (!post) return;
        state.commentBusy.add(key);
        post._commentDraft = content;
        post._commentError = '';
        renderFeed();
        try {
            const result = await api('/wallet/community/comments', {
                postId: post.id,
                displayName: displayName(),
                content
            });
            post.comments = [...(Array.isArray(post.comments) ? post.comments : []), result.comment];
            post.commentCount = Number(result.commentCount) || (Number(post.commentCount) || 0) + 1;
            post._commentDraft = '';
            state.expanded.add(key);
        } catch (error) {
            post._commentError = error.message || 'Comment could not be sent.';
        } finally {
            state.commentBusy.delete(key);
            renderFeed();
        }
    }

    async function deletePost(postId) {
        const key = String(postId);
        const post = state.posts.find(item => String(item.id) === key);
        if (!post || state.deleteBusy.has(key)) return;
        const confirmed = window.confirm('Delete this post? This cannot be undone.');
        if (!confirmed) return;
        state.deleteBusy.add(key);
        renderFeed();
        try {
            await api('/wallet/community/posts/delete', { postId: post.id });
            state.posts = state.posts.filter(item => String(item.id) !== key);
            state.expanded.delete(key);
            renderFeed();
        } catch (error) {
            state.deleteBusy.delete(key);
            post._actionError = error.message || 'This post could not be deleted.';
            renderFeed();
        }
    }

    async function toggleRepost(postId) {
        const post = state.posts.find(item => String(item.id) === String(postId));
        if (!post || post._repostBusy) return;
        post._repostBusy = true;
        const previous = Boolean(post.repostedByMe);
        post.repostedByMe = !previous;
        post.repostCount = Math.max(0, (Number(post.repostCount) || 0) + (previous ? -1 : 1));
        renderFeed();
        try {
            const result = await api('/wallet/community/posts/repost', { postId: post.id });
            post.repostedByMe = Boolean(result.repostedByMe);
            post.repostCount = Number(result.repostCount) || 0;
        } catch (error) {
            post.repostedByMe = previous;
            post.repostCount = Math.max(0, (Number(post.repostCount) || 0) + (previous ? 1 : -1));
            post._actionError = error.message || 'Repost could not be updated.';
        } finally {
            post._repostBusy = false;
            renderFeed();
        }
    }

    screen.querySelectorAll('[data-feed-scope]').forEach(button => button.addEventListener('click', () => {
        const next = button.dataset.feedScope;
        if (!['latest', 'following'].includes(next) || state.scope === next) return;
        state.scope = next;
        screen.querySelectorAll('[data-feed-scope]').forEach(item => {
            const active = item === button;
            item.classList.toggle('is-active', active);
            item.setAttribute('aria-pressed', String(active));
        });
        state.posts = [];
        state.nextCursor = null;
        fetchFeed();
    }));

    screen.addEventListener('click', event => {
        if (event.target.closest('#zeronet-claim')) {
            claimRewards();
            return;
        }
        const button = event.target.closest('[data-community-action]');
        const profileButton = event.target.closest('[data-open-profile]');
        if (profileButton?.dataset.openProfile) {
            screen.dispatchEvent(new CustomEvent('social:open-profile', { detail: { publicId: profileButton.dataset.openProfile } }));
            return;
        }
        if (!button) return;
        const action = button.dataset.communityAction;
        const card = button.closest('.community-post');
        const postId = card?.dataset.postId;
        if (action === 'close') closeFeed();
        else if (action === 'refresh') fetchFeed();
        else if (action === 'pick-image') imageInput.click();
        else if (action === 'remove-image') removeImage();
        else if (action === 'submit-post') submitPost();
        else if (action === 'load-more') fetchFeed({ append: true });
        else if (action === 'like' && postId) toggleLike(postId);
        else if (action === 'repost' && postId) toggleRepost(postId);
        else if (action === 'toggle-comments' && postId) {
            if (state.expanded.has(String(postId))) state.expanded.delete(String(postId));
            else state.expanded.add(String(postId));
            renderFeed();
            if (state.expanded.has(String(postId))) {
                const input = screen.querySelector(`.community-post[data-post-id="${CSS.escape(String(postId))}"] .community-comment-input`);
                input?.focus({ preventScroll: true });
            }
        } else if (action === 'delete-post' && postId) deletePost(postId);
    });

    screen.addEventListener('submit', event => {
        const form = event.target.closest('.community-comment-form');
        if (!form) return;
        event.preventDefault();
        submitComment(form);
    });
    document.getElementById('nav-chat')?.addEventListener('click', openFeed);
    postInput.addEventListener('input', () => {
        setComposerMessage('');
        updateComposer();
    });
    imageInput.addEventListener('change', () => selectImage(imageInput.files?.[0]));
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && state.open) closeFeed();
    });
    setDisplayName();
    updateComposer();
})();
