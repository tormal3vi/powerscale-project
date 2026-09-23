// Message board: a feed of posts, each optionally carrying a matchup -
// picked right in the composer, or arriving from the compare page's
// "Share to board" as board.html?a=&b=&fa=&fb= (which prefills the same
// picker) - with likes and one level of replies.
// Everything users write goes through escapeHtml/textContent.

renderTopbar([]);

const MAX_CHARS = 500;
const USER_COLORS = ['#E15252', '#D9A441', '#8FBF6B', '#C777D6', '#4C8DE0', '#4CC2B0'];
const HEART = '<path d="M8 13.5s-5.5-3.2-5.5-7A3 3 0 0 1 8 4.6 3 3 0 0 1 13.5 6.5c0 3.8-5.5 7-5.5 7Z"/>';
const SHIELD = (size) => `<svg width="${size}" height="${size}" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.5 14 4.5v4c0 4-2.7 6.5-6 8-3.3-1.5-6-4-6-8v-4L8 1.5Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
const ADMIN_BADGE = `<span class="admin-badge" title="Site admin">${SHIELD(10)}Admin</span>`;
const feed = document.getElementById('feed');
const loadMore = document.getElementById('load-more');
let nextBefore = null;
let me = null;
let attached = null; // {char_a, char_b, form_a, form_b} once the picker has both sides

function userColor(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return USER_COLORS[h % USER_COLORS.length];
}

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

let rosterPromise = null;
function roster() {
  if (!rosterPromise) {
    rosterPromise = Api.listCharacters().then((r) => r.characters.map((c) => ({
      ...c, _name: fold(c.name), _aliases: fold(c.aliases), _cat: fold(c.category),
    })));
  }
  return rosterPromise;
}

// Name prefix first, then anywhere in the name, then aliases, then series.
function searchRoster(all, q, excludeId) {
  const hits = [];
  for (const c of all) {
    if (c.id === excludeId) continue;
    const rank = c._name.startsWith(q) ? 0 : c._name.includes(q) ? 1
      : c._aliases.includes(q) ? 2 : c._cat.includes(q) ? 3 : -1;
    if (rank >= 0) hits.push([rank, c]);
  }
  hits.sort((x, y) => x[0] - y[0] || x[1].name.localeCompare(y[1].name));
  return hits.slice(0, 8).map((h) => h[1]);
}

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
        <span class="mu-pick-name">${escapeHtml(bareName(c.name))}<span class="mu-pick-cat"> · ${escapeHtml(own ? own[1] : c.category)}</span></span>
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
              <span class="mu-result-cat">${escapeHtml(c.category)}</span>
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

function avatarHtml(name, cls, isAdmin) {
  return `<span class="${cls}${isAdmin ? ' is-admin' : ''}" style="background:${userColor(name)}">${escapeHtml(initialFor(name))}</span>`;
}

function authorHtml(post, cls) {
  return `<span class="${cls}">${escapeHtml(post.author)}</span>${post.author_is_admin ? ADMIN_BADGE : ''}`;
}

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
  el.innerHTML = `
    ${avatarHtml(reply.author, 'reply-avatar', reply.author_is_admin)}
    <div class="reply-main">
      <div class="reply-head">${authorHtml(reply, 'reply-author')} <span class="post-time">· ${timeAgo(reply.created_at)}</span>
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
  const inner = `
    ${avatarHtml(post.author, 'post-avatar', post.author_is_admin)}
    <div class="post-main">
      <div class="post-head">
        ${authorHtml(post, 'post-author')}
        <span class="post-time" title="${escapeHtml(new Date(post.created_at).toLocaleString())}">· ${timeAgo(post.created_at)}</span>
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
  repliesBtn.addEventListener('click', async () => {
    const open = thread.style.display !== 'none';
    thread.style.display = open ? 'none' : '';
    repliesBtn.classList.toggle('open', !open);
    if (open) return;
    thread.innerHTML = '<div class="post-time">Loading…</div>';
    try {
      const t = await Api.getThread(post.id);
      thread.innerHTML = '';
      const list = document.createElement('div');
      list.className = 'reply-list';
      t.replies.forEach((r) => list.appendChild(replyEl(r)));
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

async function loadPage() {
  loadMore.disabled = true;
  try {
    const res = await Api.listPosts(nextBefore);
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

(async () => {
  me = await currentUser();
  document.getElementById('composer-slot').appendChild(composerEl((post) => {
    const empty = feed.querySelector('.board-empty');
    if (empty) empty.remove();
    feed.prepend(postEl(post));
  }));
  loadPage();
})();
