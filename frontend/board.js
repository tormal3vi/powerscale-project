// Message board: a feed of posts, each optionally carrying a matchup
// (arriving here from the compare page's "Share to board" as
// board.html?a=&b=&fa=&fb=), with likes and one level of replies.
// Everything users write goes through escapeHtml/textContent.

renderTopbar([]);

const MAX_CHARS = 500;
const USER_COLORS = ['#E15252', '#D9A441', '#8FBF6B', '#C777D6', '#4C8DE0', '#4CC2B0'];
const HEART = '<path d="M8 13.5s-5.5-3.2-5.5-7A3 3 0 0 1 8 4.6 3 3 0 0 1 13.5 6.5c0 3.8-5.5 7-5.5 7Z"/>';
const feed = document.getElementById('feed');
const loadMore = document.getElementById('load-more');
let nextBefore = null;
let me = null;
let attached = null; // {label, payload} from the URL

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

function matchupHtml(m) {
  const verdict = m.overruled_winner
    ? `<div class="mu-overruled">${escapeHtml(m.overruled_winner)} wins — overruled<span class="mu-series"> by admins</span></div>
       <div class="mu-calc">Calculator's estimate: ${escapeHtml(m.calc_verdict)}</div>`
    : `<div class="mu-verdict">${escapeHtml(m.calc_verdict)}</div>`;
  return `
    <a class="post-matchup ${m.overruled_winner ? 'overruled' : ''}" href="${matchupHref(m)}">
      <div class="mu-title">${matchupSideHtml(m.label_a, m.name_a, m.category_a)} vs ${matchupSideHtml(m.label_b, m.name_b, m.category_b)}</div>
      ${verdict}
    </a>`;
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
      <span class="composer-count"></span>
      <button class="btn-gold">Post</button>
    </div>
    <div class="form-error"></div>`;
  const ta = wrap.querySelector('textarea');
  const count = wrap.querySelector('.composer-count');
  const btn = wrap.querySelector('button.btn-gold');
  const err = wrap.querySelector('.form-error');
  const updateCount = () => { count.textContent = `${ta.value.length}/${MAX_CHARS}`; };
  ta.addEventListener('input', updateCount);
  updateCount();

  const attachSlot = wrap.querySelector('.composer-attach');
  const renderAttach = () => {
    attachSlot.innerHTML = '';
    if (!attached) return;
    attachSlot.innerHTML = `<span class="attach-chip"><span></span><button aria-label="Remove matchup">×</button></span>`;
    attachSlot.querySelector('.attach-chip > span').textContent = `Matchup: ${attached.label}`;
    attachSlot.querySelector('button').addEventListener('click', () => {
      attached = null;
      history.replaceState(null, '', 'board.html');
      renderAttach();
    });
  };
  renderAttach();

  btn.addEventListener('click', async () => {
    const body = ta.value.trim();
    if (!body) { err.textContent = 'Write something first.'; return; }
    btn.disabled = true;
    err.textContent = '';
    const payload = { body };
    if (attached) Object.assign(payload, attached.payload);
    try {
      const post = await Api.createPost(payload);
      ta.value = '';
      updateCount();
      if (attached) {
        attached = null;
        history.replaceState(null, '', 'board.html');
        renderAttach();
      }
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

function avatarHtml(name, cls) {
  return `<span class="${cls}" style="background:${userColor(name)}">${escapeHtml(initialFor(name))}</span>`;
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
    ${avatarHtml(reply.author, 'reply-avatar')}
    <div class="reply-main">
      <div class="reply-head"><strong>${escapeHtml(reply.author)}</strong> <span class="post-time">· ${timeAgo(reply.created_at)}</span>
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
  el.className = 'post';
  el.innerHTML = `
    ${avatarHtml(post.author, 'post-avatar')}
    <div class="post-main">
      <div class="post-head">
        <span class="post-author">${escapeHtml(post.author)}</span>
        <span class="post-time" title="${escapeHtml(new Date(post.created_at).toLocaleString())}">· ${timeAgo(post.created_at)}</span>
        ${post.can_delete ? '<button class="post-delete">Delete</button>' : ''}
      </div>
      <div class="post-body"></div>
      ${post.matchup ? matchupHtml(post.matchup) : ''}
      <div class="post-actions">
        <button class="post-like ${post.liked_by_me ? 'liked' : ''}" aria-label="Like">
          <svg width="16" height="16" viewBox="0 0 16 16">${HEART}</svg><span>${post.like_count}</span>
        </button>
        <button class="post-replies">Replies (<span>${post.reply_count}</span>)</button>
      </div>
      <div class="post-thread" style="display:none;"></div>
    </div>`;
  el.querySelector('.post-body').textContent = post.body;

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

async function loadAttachment() {
  const p = new URLSearchParams(location.search);
  const a = Number(p.get('a'));
  const b = Number(p.get('b'));
  if (!a || !b) return;
  try {
    const v = await Api.compare(a, b, p.get('fa'), p.get('fb'));
    attached = {
      label: `${shortName(v.character_a)} (${v.form_a}) vs ${shortName(v.character_b)} (${v.form_b})`,
      payload: { char_a: a, char_b: b, form_a: v.form_a, form_b: v.form_b },
    };
  } catch { /* bad ids in the URL - just post without a matchup */ }
}

loadMore.addEventListener('click', loadPage);

(async () => {
  me = await currentUser();
  await loadAttachment();
  document.getElementById('composer-slot').appendChild(composerEl((post) => {
    const empty = feed.querySelector('.board-empty');
    if (empty) empty.remove();
    feed.prepend(postEl(post));
  }));
  loadPage();
})();
