// Message board: a feed of posts, each optionally carrying a matchup -
// picked right in the composer, or arriving from the compare page's
// "Share to board" as board.html?a=&b=&fa=&fb= (which prefills the same
// picker) - with likes and one level of replies.
// Everything users write goes through escapeHtml/textContent.

renderTopbar([]);

const MAX_CHARS = 500;
const HEART = '<path d="M8 13.5s-5.5-3.2-5.5-7A3 3 0 0 1 8 4.6 3 3 0 0 1 13.5 6.5c0 3.8-5.5 7-5.5 7Z"/>';
const SHIELD = (size) => `<svg width="${size}" height="${size}" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.2 14 3.8v3.6c0 3.7-2.5 6.1-6 7.4-3.5-1.3-6-3.7-6-7.4V3.8L8 1.2Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
const ADMIN_BADGE = adminBadgeHtml();
const feed = document.getElementById('feed');
const loadMore = document.getElementById('load-more');
let nextBefore = null;
let me = null;
let attached = null; // {char_a, char_b, form_a, form_b} once the picker has both sides

function timeAgo(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d`;
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function matchupHref(m) {
  const p = new URLSearchParams({ a: m.char_a, b: m.char_b });
  if (m.form_a) p.set('fa', m.form_a);
  if (m.form_b) p.set('fb', m.form_b);
  return `compare.html?${p}`;
}

// "Kratos (God of War)" - the page's own qualifier when it has one
// ("Dante (Devil May Cry)", "Ichigo Kurosaki (Pre-Timeskip)"), else the
// series. The part in brackets is dropped on phones, per the design.
function matchupSideHtml(label, fullName, category) {
  const own = /\(([^)]*)\)/.exec(shortName(fullName));
  const extra = own ? own[1] : category;
  return `${escapeHtml(label)}${extra ? `<span class="mu-series"> (${escapeHtml(extra)})</span>` : ''}`;
}

// The card links to the full comparison; the composer's preview of it
// doesn't (a click there would throw away the half-written post).
function matchupHtml(m, { link = true } = {}) {
  const verdict = m.overruled_winner
    ? `<div class="mu-overruled">${escapeHtml(m.overruled_winner)} wins — overruled<span class="mu-series"> by admins</span></div>
       <div class="mu-calc">Calculator's estimate: ${escapeHtml(m.calc_verdict)}</div>`
    : `<div class="mu-verdict">${escapeHtml(m.calc_verdict)}</div>`;
  const tag = link ? 'a' : 'div';
  return `
    <${tag} class="post-matchup ${m.overruled_winner ? 'overruled' : ''}" ${link ? `href="${matchupHref(m)}"` : ''}>
      <div class="mu-title">${matchupSideHtml(m.label_a, m.name_a, m.category_a)} vs ${matchupSideHtml(m.label_b, m.name_b, m.category_b)}</div>
      ${verdict}
    </${tag}>`;
}

// --- matchup picker ---------------------------------------------------------
// Both sides chosen right in the composer: search (names, aliases, series;
// accent-insensitive), a form dropdown for multi-form characters, and a
// live preview of the exact card the post will carry.

function matchupPickerEl({ onChange, onClose }) {
  const el = document.createElement('div');
  el.className = 'mu-picker';
  el.innerHTML = `
    <div class="mu-picker-head">
      <span class="mu-picker-title">Matchup</span>
      <span class="mu-picker-tools">
        <button type="button" data-act="random">Random</button>
        <button type="button" data-act="remove">Remove</button>
      </span>
    </div>
    <div class="mu-picker-sides">
      <div class="mu-slot" data-side="a"></div>
      <span class="mu-picker-vs">vs</span>
      <div class="mu-slot" data-side="b"></div>
    </div>
    <div class="mu-picker-preview"></div>`;
  const picks = { a: null, b: null }; // {c: full character, formIndex}
  const preview = el.querySelector('.mu-picker-preview');
  const slotEl = (side) => el.querySelector(`.mu-slot[data-side="${side}"]`);
  const other = (side) => picks[side === 'a' ? 'b' : 'a'];
  let seq = 0; // drops stale previews when picks change mid-request

  async function refresh() {
    const my = ++seq;
    if (!picks.a || !picks.b) { preview.innerHTML = ''; onChange(null); return; }
    const fa = picks.a.c.forms[picks.a.formIndex].name;
    const fb = picks.b.c.forms[picks.b.formIndex].name;
    preview.innerHTML = '<div class="mu-picker-note">Getting the verdict…</div>';
    onChange(null);
    try {
      const m = await Api.matchupPreview(picks.a.c.id, picks.b.c.id, fa, fb);
      if (my !== seq) return;
      preview.innerHTML = matchupHtml(m, { link: false });
      onChange({ char_a: m.char_a, char_b: m.char_b, form_a: m.form_a, form_b: m.form_b });
    } catch (err) {
      if (my !== seq) return;
      preview.innerHTML = `<div class="form-error">${escapeHtml(err.message)}</div>`;
    }
  }

  async function choose(side, id, formName) {
    slotEl(side).innerHTML = '<div class="mu-picker-note">Loading…</div>';
    try {
      const c = await Api.getCharacter(id);
      let formIndex = formName ? c.forms.findIndex((f) => f.name === formName) : -1;
      if (formIndex < 0) formIndex = defaultFormIndex(c.forms);
      picks[side] = { c, formIndex };
    } catch {
      picks[side] = null; // bad id (e.g. from an old link) - just search instead
    }
    renderSlot(side);
    refresh();
  }

  function renderPicked(slot, side) {
    const { c, formIndex } = picks[side];
    const own = /\(([^)]*)\)/.exec(shortName(c.name));
    slot.innerHTML = `
      <div class="mu-pick">
        <span class="mu-pick-dot" style="background:${accentFor(c.id)}"></span>
        <span class="mu-pick-name">${escapeHtml(bareName(c.name))}<span class="mu-pick-cat"> · ${escapeHtml(own ? own[1] : seriesLabel(c))}</span></span>
        <button type="button" class="mu-pick-clear" aria-label="Change character">×</button>
      </div>
      ${c.forms.length > 1 ? '<select class="mu-pick-form" aria-label="Form"></select>' : ''}`;
    slot.querySelector('.mu-pick-clear').addEventListener('click', () => {
      picks[side] = null;
      renderSlot(side, { focus: true });
      refresh();
    });
    const select = slot.querySelector('select');
    if (select) {
      c.forms.forEach((f, i) => {
        const opt = document.createElement('option');
        opt.value = i;
        opt.textContent = f.name;
        select.appendChild(opt);
      });
      select.value = formIndex;
      select.addEventListener('change', () => { picks[side].formIndex = Number(select.value); refresh(); });
    }
  }

  function renderSearch(slot, side, focus) {
    slot.innerHTML = `
      <input type="text" class="mu-search" autocomplete="off" spellcheck="false"
        placeholder="${side === 'a' ? 'First' : 'Second'} character…" aria-label="${side === 'a' ? 'First' : 'Second'} character">
      <div class="mu-results" role="listbox"></div>`;
    const input = slot.querySelector('input');
    const results = slot.querySelector('.mu-results');
    let items = [];
    let active = 0;
    const paint = () => results.querySelectorAll('.mu-result').forEach((b, i) => b.classList.toggle('active', i === active));
    const show = async () => {
      const q = fold(input.value.trim());
      if (!q) { results.innerHTML = ''; items = []; return; }
      if (!items.length) results.innerHTML = '<div class="mu-picker-note">Searching…</div>';
      const all = await roster();
      if (fold(input.value.trim()) !== q) return; // typed on while loading
      items = searchRoster(all, q, other(side)?.c.id);
      active = 0;
      results.innerHTML = items.length
        ? items.map((c, i) => `
            <button type="button" class="mu-result" role="option" data-i="${i}">
              <span class="mu-result-name">${escapeHtml(shortName(c.name))}</span>
              <span class="mu-result-cat">${escapeHtml(seriesLabel(c))}</span>
            </button>`).join('')
        : '<div class="mu-picker-note">No characters match that.</div>';
      paint();
    };
    input.addEventListener('input', show);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!items.length) return;
        e.preventDefault();
        active = (active + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
        paint();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (items[active]) choose(side, items[active].id);
      }
    });
    results.addEventListener('click', (e) => {
      const b = e.target.closest('.mu-result');
      if (b) choose(side, items[Number(b.dataset.i)].id);
    });
    if (focus) input.focus();
  }

  function renderSlot(side, { focus = false } = {}) {
    const slot = slotEl(side);
    if (picks[side]) renderPicked(slot, side);
    else renderSearch(slot, side, focus);
  }

  el.querySelector('[data-act="random"]').addEventListener('click', async () => {
    const pool = (await roster()).filter((c) => c.scorable);
    const a = pool[Math.floor(Math.random() * pool.length)];
    let b = a;
    while (b.id === a.id) b = pool[Math.floor(Math.random() * pool.length)];
    picks.a = null; picks.b = null;
    await Promise.all([choose('a', a.id), choose('b', b.id)]);
  });
  el.querySelector('[data-act="remove"]').addEventListener('click', () => {
    seq += 1;
    onChange(null);
    onClose();
  });

  renderSlot('a');
  renderSlot('b');
  el.prefill = (a, b, fa, fb) => Promise.all([choose('a', a, fa), choose('b', b, fb)]);
  el.focusFirst = () => slotEl('a').querySelector('input')?.focus();
  return el;
}

// --- composer ---------------------------------------------------------------

function loginPrompt() {
  const next = encodeURIComponent('board.html' + location.search);
  const el = document.createElement('div');
  el.className = 'composer composer-login';
  el.innerHTML = `<a href="login.html?next=${next}">Log in</a> or <a href="login.html?mode=register&next=${next}">create an account</a> to post.`;
  return el;
}

function composerEl(onPosted) {
  if (!me) return loginPrompt();
  const wrap = document.createElement('div');
  wrap.className = 'composer';
  wrap.innerHTML = `
    <div class="composer-attach"></div>
    <label><span class="visually-hidden">Write a post</span><textarea rows="3" maxlength="${MAX_CHARS}" placeholder="What's your take?"></textarea></label>
    <div class="composer-row">
      <button type="button" class="composer-add-mu">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        Matchup
      </button>
      <span class="composer-right">
        <span class="composer-count"></span>
        <button class="btn-gold">Post</button>
      </span>
    </div>
    <div class="form-error"></div>`;
  const ta = wrap.querySelector('textarea');
  const count = wrap.querySelector('.composer-count');
  const btn = wrap.querySelector('button.btn-gold');
  const err = wrap.querySelector('.form-error');
  const addBtn = wrap.querySelector('.composer-add-mu');
  const attachSlot = wrap.querySelector('.composer-attach');
  const updateCount = () => { count.textContent = `${ta.value.length}/${MAX_CHARS}`; };
  ta.addEventListener('input', updateCount);
  updateCount();

  let picker = null;
  const closePicker = () => {
    attached = null;
    picker = null;
    attachSlot.innerHTML = '';
    addBtn.style.display = '';
    if (location.search) history.replaceState(null, '', 'board.html');
  };
  const openPicker = () => {
    roster(); // start the ~1600-character fetch before the first keystroke
    picker = matchupPickerEl({ onChange: (m) => { attached = m; }, onClose: closePicker });
    attachSlot.innerHTML = '';
    attachSlot.appendChild(picker);
    addBtn.style.display = 'none';
    return picker;
  };
  addBtn.addEventListener('click', () => openPicker().focusFirst());

  // From Compare's "Share to board": open the picker already filled in.
  const p = new URLSearchParams(location.search);
  if (Number(p.get('a')) && Number(p.get('b'))) {
    openPicker().prefill(Number(p.get('a')), Number(p.get('b')), p.get('fa'), p.get('fb'));
  }

  btn.addEventListener('click', async () => {
    const body = ta.value.trim();
    if (!body) { err.textContent = 'Write something first.'; return; }
    if (picker && !attached) { err.textContent = 'Finish picking the matchup, or remove it.'; return; }
    btn.disabled = true;
    err.textContent = '';
    const payload = { body, ...(attached || {}) };
    try {
      const post = await Api.createPost(payload);
      ta.value = '';
      updateCount();
      if (picker) closePicker();
      onPosted(post);
    } catch (e) {
      err.textContent = e.message;
    } finally {
      btn.disabled = false;
    }
  });
  return wrap;
}

function replyComposerEl(parentId, onPosted) {
  if (!me) return document.createElement('span');
  const form = document.createElement('form');
  form.className = 'reply-composer';
  form.innerHTML = `
    <label class="visually-hidden" for="reply-${parentId}">Write a reply</label>
    <input id="reply-${parentId}" type="text" maxlength="${MAX_CHARS}" placeholder="Reply…" autocomplete="off">
    <button class="reply-btn">Reply</button>`;
  const input = form.querySelector('input');
  const btn = form.querySelector('button');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = input.value.trim();
    if (!body) return;
    btn.disabled = true;
    try {
      const reply = await Api.createPost({ body, parent_id: parentId });
      input.value = '';
      onPosted(reply);
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
    }
  });
  return form;
}

// --- posts -----------------------------------------------------------------------

// Author name and avatar both open that user's profile popover.
function avatarHtml(post, cls) {
  return `<button type="button" class="author-hit author-avatar-hit" data-user="${escapeHtml(post.author)}" aria-label="View ${escapeHtml(post.author)}'s profile">${
    userAvatarHtml(post.author, post.author_avatar, cls, { admin: post.author_is_admin })}</button>`;
}

function favoriteChipHtml(fav, cls, px) {
  if (!fav) return '';
  return `<span class="${cls}" title="Favorite character: ${escapeHtml(fav.name)}">
    <span class="${cls}-tile${fav.image_url ? ' has-pic' : ''}" style="background:${accentFor(fav.id)}">${characterTileInner(fav.name, fav.image_url, px)}</span>
    <span class="${cls}-name">${escapeHtml(fav.name)}</span></span>`;
}

function authorHtml(post, cls) {
  return `<button type="button" class="author-hit ${cls}" data-user="${escapeHtml(post.author)}">${escapeHtml(post.author)}</button>${
    post.author_is_admin ? ADMIN_BADGE : ''}${favoriteChipHtml(post.author_favorite, 'fav-mini', 32)}`;
}

// --- author profile popover -------------------------------------------------

const profileCache = new Map();
let openPopover = null;

function closePopover() {
  if (openPopover) { openPopover.remove(); openPopover = null; }
}

async function showProfilePopover(trigger) {
  const username = trigger.dataset.user;
  const host = trigger.closest('.post, .reply');
  if (openPopover && openPopover.dataset.user === username && openPopover.parentNode === host) {
    closePopover(); // clicking the same author again closes it
    return;
  }
  closePopover();
  const pop = document.createElement('div');
  pop.className = 'user-pop';
  pop.dataset.user = username;
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', `${username}'s profile`);
  pop.innerHTML = '<div class="user-pop-loading">Loading…</div>';
  // Just under the row with the author's name, lined up with the text.
  const head = trigger.closest('.post-head, .reply-head') || host.querySelector('.post-head, .reply-head');
  const main = head.parentElement;
  pop.style.top = `${head.offsetTop + head.offsetHeight + 10}px`;
  pop.style.left = `${main.offsetLeft}px`;
  host.appendChild(pop);
  openPopover = pop;
  try {
    if (!profileCache.has(username)) profileCache.set(username, Api.userProfile(username));
    const p = await profileCache.get(username);
    if (openPopover !== pop) return;
    pop.innerHTML = `
      <div class="user-pop-head">
        ${userAvatarHtml(p.username, p.avatar_url, 'user-pop-avatar', { admin: p.is_admin })}
        <div class="user-pop-id">
          <div class="user-pop-name-row"><span class="user-pop-name">${escapeHtml(p.username)}</span>${p.is_admin ? adminBadgeHtml({ solid: true }) : ''}</div>
          <div class="user-pop-meta">${monthYear(p.member_since)} · ${p.post_count} post${p.post_count === 1 ? '' : 's'}</div>
        </div>
      </div>
      ${p.bio ? `<div class="user-pop-bio">${escapeHtml(p.bio)}</div>` : ''}
      ${favoriteChipHtml(p.favorite, 'fav-pop', 44)}`;
  } catch (err) {
    profileCache.delete(username);
    if (openPopover === pop) pop.innerHTML = `<div class="user-pop-loading">${escapeHtml(err.message)}</div>`;
  }
}

document.addEventListener('click', (e) => {
  const trigger = e.target.closest('.author-hit');
  if (trigger) { showProfilePopover(trigger); return; }
  if (openPopover && !openPopover.contains(e.target)) closePopover();
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePopover(); });

// The card an admin overrule posts on its own: what was ruled (kept as
// posted, even if the overrule is later changed or lifted), on which
// matchup, and what the calculator had said.
function rulingHtml(post) {
  const m = post.matchup;
  const r = post.ruling;
  const status = r && r.status !== 'current'
    ? `<span class="ruling-status">${r.status === 'lifted' ? 'Since lifted' : 'Since changed'}</span>` : '';
  const strip = `<div class="ruling-strip">${SHIELD(13)}<span>Admin overrule</span>${status}</div>`;
  if (!m || !r) return { strip, main: '' }; // a character was removed since
  return {
    strip,
    main: `
      <div class="ruling-headline">${escapeHtml(r.winner)} wins</div>
      <a class="ruling-matchup" href="${matchupHref(m)}">
        <span class="ruling-vs">${matchupSideHtml(m.label_a, m.name_a, m.category_a)} vs ${matchupSideHtml(m.label_b, m.name_b, m.category_b)}</span>
        <span class="ruling-calc">Calculator's estimate: ${escapeHtml(m.calc_verdict)}</span>
      </a>`,
  };
}

function deleteHandler(el, post, isReply) {
  return async () => {
    if (!confirm(isReply ? 'Delete this reply?' : 'Delete this post and all its replies?')) return;
    try { await Api.deletePost(post.id); el.remove(); } catch (err) { alert(err.message); }
  };
}

function replyEl(reply) {
  const el = document.createElement('div');
  el.className = 'reply';
  el.dataset.id = reply.id;
  el.innerHTML = `
    ${avatarHtml(reply, 'reply-avatar')}
    <div class="reply-main">
      <div class="reply-head">${authorHtml(reply, 'reply-author')} <span class="post-time" data-ts="${escapeHtml(reply.created_at)}">· ${timeAgo(reply.created_at)}</span>
        ${reply.can_delete ? '<button class="post-delete">Delete</button>' : ''}</div>
      <div class="reply-body"></div>
    </div>`;
  el.querySelector('.reply-body').textContent = reply.body;
  const del = el.querySelector('.post-delete');
  if (del) del.addEventListener('click', deleteHandler(el, reply, true));
  return el;
}

function postEl(post) {
  const el = document.createElement('article');
  const isRuling = post.kind === 'overrule';
  const ruling = isRuling ? rulingHtml(post) : null;
  el.className = 'post' + (isRuling ? ' post-ruling' : '') +
    (isRuling && post.ruling && post.ruling.status !== 'current' ? ' stale' : '');
  el.dataset.id = post.id;
  const inner = `
    ${avatarHtml(post, 'post-avatar')}
    <div class="post-main">
      <div class="post-head">
        ${authorHtml(post, 'post-author')}
        <span class="post-time" data-ts="${escapeHtml(post.created_at)}" title="${escapeHtml(new Date(post.created_at).toLocaleString())}">· ${timeAgo(post.created_at)}</span>
        ${post.can_delete ? '<button class="post-delete">Delete</button>' : ''}
      </div>
      ${isRuling ? ruling.main : ''}
      <div class="post-body${isRuling ? ' ruling-note' : ''}"></div>
      ${!isRuling && post.matchup ? matchupHtml(post.matchup) : ''}
      <div class="post-actions">
        <button class="post-like ${post.liked_by_me ? 'liked' : ''}" aria-label="Like">
          <svg width="16" height="16" viewBox="0 0 16 16">${HEART}</svg><span>${post.like_count}</span>
        </button>
        <button class="post-replies">Replies (<span>${post.reply_count}</span>)</button>
      </div>
      <div class="post-thread" style="display:none;"></div>
    </div>`;
  el.innerHTML = isRuling ? `${ruling.strip}<div class="ruling-inner">${inner}</div>` : inner;
  const bodyEl = el.querySelector('.post-body');
  if (isRuling && !post.body) bodyEl.remove(); // a ruling saved without a reason
  else bodyEl.textContent = isRuling ? `“${post.body}”` : post.body;

  el.querySelector('.post-like').addEventListener('click', async (e) => {
    if (!me) { location.href = `login.html?next=${encodeURIComponent('board.html')}`; return; }
    const btn = e.currentTarget;
    try {
      const r = await Api.likePost(post.id);
      btn.classList.toggle('liked', r.liked);
      btn.querySelector('span').textContent = r.like_count;
    } catch (err) { alert(err.message); }
  });

  const del = el.querySelector('.post-head .post-delete');
  if (del) del.addEventListener('click', deleteHandler(el, post, false));

  const repliesBtn = el.querySelector('.post-replies');
  const thread = el.querySelector('.post-thread');
  let list = null; // the open thread's replies, once loaded
  // Adds replies not shown yet and drops deleted ones - on opening the
  // thread, and again whenever the live Board sees it change.
  const showReplies = (replies) => {
    const ids = new Set(replies.map((r) => String(r.id)));
    list.querySelectorAll(':scope > .reply').forEach((r) => { if (!ids.has(r.dataset.id)) r.remove(); });
    replies.forEach((r) => { if (!list.querySelector(`:scope > [data-id="${r.id}"]`)) list.appendChild(replyEl(r)); });
    repliesBtn.querySelector('span').textContent = replies.length;
  };
  repliesBtn.addEventListener('click', async () => {
    const open = thread.style.display !== 'none';
    thread.style.display = open ? 'none' : '';
    repliesBtn.classList.toggle('open', !open);
    if (open) return;
    list = null;
    thread.innerHTML = '<div class="post-time">Loading…</div>';
    try {
      const t = await Api.getThread(post.id);
      thread.innerHTML = '';
      list = document.createElement('div');
      list.className = 'reply-list';
      showReplies(t.replies);
      thread.appendChild(list);
      thread.appendChild(replyComposerEl(post.id, (reply) => {
        list.appendChild(replyEl(reply));
        const n = repliesBtn.querySelector('span');
        n.textContent = Number(n.textContent) + 1;
      }));
    } catch (err) {
      thread.innerHTML = `<div class="form-error">${escapeHtml(err.message)}</div>`;
    }
  });

  // Hooks for the live Board (see the end of this file).
  const threadOpen = () => list !== null && thread.style.display !== 'none';
  el.live = {
    update(p) {
      const like = el.querySelector('.post-like');
      like.classList.toggle('liked', p.liked_by_me);
      like.querySelector('span').textContent = p.like_count;
      if (!threadOpen()) repliesBtn.querySelector('span').textContent = p.reply_count;
    },
    // Open, and (when the post is on the newest page) its reply count moved.
    threadOutdated(p) {
      return threadOpen() && (!p || list.querySelectorAll(':scope > .reply').length !== p.reply_count);
    },
    async refreshThread() {
      try {
        const { replies } = await Api.getThread(post.id);
        if (threadOpen()) showReplies(replies);
      } catch (err) {
        if (err.status === 404) el.remove(); // deleted since
      }
    },
  };
  return el;
}

function emptyStateEl() {
  const el = document.createElement('div');
  el.className = 'board-empty';
  el.innerHTML = `
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none"><path d="M4 5h16v11H8l-4 4V5Z" stroke="#3A352C" stroke-width="1.4" stroke-linejoin="round"/></svg>
    <div class="board-empty-title">No posts yet</div>
    <div class="board-empty-text">Be the first to start a matchup debate on the board.</div>`;
  return el;
}

// The first page is requested straight away, alongside the login check,
// instead of waiting for it.
let firstPage = Api.listPosts();

async function loadPage() {
  loadMore.disabled = true;
  try {
    const pending = firstPage;
    firstPage = null; // used once, even if it failed
    const res = await (pending || Api.listPosts(nextBefore));
    if (!res.posts.length && !nextBefore) feed.appendChild(emptyStateEl());
    res.posts.forEach((p) => feed.appendChild(postEl(p)));
    nextBefore = res.next_before;
    loadMore.style.display = nextBefore ? '' : 'none';
  } catch (err) {
    feed.innerHTML = `<div class="error-state">Failed to load posts: ${escapeHtml(err.message)}</div>`;
  } finally {
    loadMore.disabled = false;
  }
}

loadMore.addEventListener('click', loadPage);

// --- live updates ------------------------------------------------------------------
// While the Board is open and visible, it asks every 10s whether anything
// changed - a tiny answer the server keeps in memory. Only when something
// did does it fetch the newest page: new posts wait behind a "new posts"
// pill, so nothing jumps while you read; likes, reply counts, open
// threads and deletions update in place.

const LIVE_EVERY_MS = 10000;
const newPostsBtn = document.getElementById('new-posts');
const baseTitle = document.title;
let boardVersion = null; // null until the first check, which always refreshes
let waiting = []; // new posts behind the pill, newest first
let liveTimer = null;
let liveBusy = false;
let liveFailures = 0;

const feedPosts = () => [...feed.querySelectorAll(':scope > .post')];

function updatePill() {
  const n = waiting.length;
  newPostsBtn.hidden = !n;
  newPostsBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>${n} new post${n === 1 ? '' : 's'}`;
  document.title = n ? `(${n}) ${baseTitle}` : baseTitle;
}

function showWaiting() {
  if (!waiting.length) return;
  const empty = feed.querySelector('.board-empty');
  if (empty) empty.remove();
  [...waiting].reverse().forEach((p) => feed.prepend(postEl(p)));
  waiting = [];
  updatePill();
}

newPostsBtn.addEventListener('click', () => {
  showWaiting();
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

function applyNewestPage(res) {
  const byId = new Map(res.posts.map((p) => [p.id, p]));
  const shown = feedPosts();
  const newestShown = Math.max(0, ...shown.map((el) => Number(el.dataset.id)));
  waiting = res.posts.filter((p) => p.id > newestShown);
  updatePill();
  // The page covers every post down to its oldest; one missing from that
  // range was deleted.
  const oldest = res.posts.length ? res.posts[res.posts.length - 1].id : Infinity;
  for (const el of shown) {
    const p = byId.get(Number(el.dataset.id));
    if (p) el.live.update(p);
    else if (Number(el.dataset.id) >= oldest) { el.remove(); continue; }
    if (el.live.threadOutdated(p)) el.live.refreshThread();
  }
  if (!res.posts.length && !feed.querySelector('.post, .board-empty')) feed.appendChild(emptyStateEl());
}

function refreshTimes() {
  feed.querySelectorAll('.post-time[data-ts]').forEach((t) => { t.textContent = `· ${timeAgo(t.dataset.ts)}`; });
}

function scheduleLive() {
  clearTimeout(liveTimer);
  if (document.hidden) return; // resumes on visibilitychange
  // Back off while the server is unreachable (asleep, or a deploy).
  liveTimer = setTimeout(liveCheck, LIVE_EVERY_MS * 2 ** Math.min(liveFailures, 3));
}

async function liveCheck() {
  if (liveBusy) return;
  liveBusy = true;
  try {
    const { version } = await Api.boardVersion();
    if (version !== boardVersion) {
      applyNewestPage(await Api.listPosts());
      // Only once that worked, and the version from BEFORE the fetch: a
      // change during it still differs next time, and a failed fetch retries.
      boardVersion = version;
    }
    liveFailures = 0;
  } catch {
    liveFailures += 1;
  } finally {
    liveBusy = false;
    refreshTimes();
    scheduleLive();
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) clearTimeout(liveTimer);
  else liveCheck(); // back on the tab: catch up straight away
});

(async () => {
  me = await currentUser();
  document.getElementById('composer-slot').appendChild(composerEl((post) => {
    showWaiting(); // anything newer than what's shown goes under your post
    const empty = feed.querySelector('.board-empty');
    if (empty) empty.remove();
    feed.prepend(postEl(post));
  }));
  await loadPage();
  scheduleLive();
})();
