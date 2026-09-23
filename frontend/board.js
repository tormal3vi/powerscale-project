// Message board: a feed of posts, each optionally carrying a matchup
// (arriving here from the compare page's "Share to board" as
// board.html?a=&b=&fa=&fb=), with likes and one level of replies.
// Everything users write goes through escapeHtml/textContent.

renderTopbar([]);

const MAX_CHARS = 500;
const feed = document.getElementById('feed');
const loadMore = document.getElementById('load-more');
let nextBefore = null;
let me = null;
let attached = null; // {char_a, char_b, form_a, form_b, label} from the URL

function nameHue(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h;
}

function timeAgo(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d`;
  return new Date(iso).toLocaleDateString();
}

function matchupHref(m) {
  const p = new URLSearchParams({ a: m.char_a, b: m.char_b });
  if (m.form_a) p.set('fa', m.form_a);
  if (m.form_b) p.set('fb', m.form_b);
  return `compare.html?${p}`;
}

// --- composer ---------------------------------------------------------------

function composerEl({ parentId = null, placeholder = "What's your take?", onPosted }) {
  const wrap = document.createElement('div');
  wrap.className = parentId ? 'composer composer-reply' : 'composer';
  if (!me) {
    const next = encodeURIComponent('board.html' + location.search);
    wrap.innerHTML = `<div class="composer-login"><a href="login.html?next=${next}">Log in</a> or <a href="login.html?mode=register&next=${next}">create an account</a> to post.</div>`;
    return wrap;
  }
  wrap.innerHTML = `
    <textarea maxlength="${MAX_CHARS}" rows="${parentId ? 2 : 3}"></textarea>
    <div class="composer-attach"></div>
    <div class="composer-row">
      <span class="composer-count"></span>
      <button class="compare-bar-cta">${parentId ? 'Reply' : 'Post'}</button>
    </div>
    <div class="add-character-status error"></div>`;
  const ta = wrap.querySelector('textarea');
  const count = wrap.querySelector('.composer-count');
  const btn = wrap.querySelector('button');
  const err = wrap.querySelector('.add-character-status');
  ta.placeholder = placeholder;
  const updateCount = () => { count.textContent = `${ta.value.length}/${MAX_CHARS}`; };
  ta.addEventListener('input', updateCount);
  updateCount();

  const attachSlot = wrap.querySelector('.composer-attach');
  const renderAttach = () => {
    attachSlot.innerHTML = '';
    if (parentId || !attached) return;
    attachSlot.innerHTML = `<span class="attach-chip">Matchup: <strong></strong> <button aria-label="Remove matchup">×</button></span>`;
    attachSlot.querySelector('strong').textContent = attached.label;
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
    const payload = { body, parent_id: parentId };
    if (!parentId && attached) Object.assign(payload, attached.payload);
    try {
      const post = await Api.createPost(payload);
      ta.value = '';
      updateCount();
      if (!parentId && attached) {
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

// --- posts -----------------------------------------------------------------------

function postEl(post, { isReply = false } = {}) {
  const el = document.createElement('article');
  el.className = isReply ? 'post post-reply' : 'post';
  el.innerHTML = `
    <div class="post-head">
      <span class="post-avatar" style="background:hsl(${nameHue(post.author) % 360}, 55%, 58%)">${escapeHtml(initialFor(post.author))}</span>
      <span class="post-author">${escapeHtml(post.author)}</span>
      <span class="post-time" title="${escapeHtml(new Date(post.created_at).toLocaleString())}">${timeAgo(post.created_at)}</span>
      ${post.can_delete ? '<button class="post-delete">Delete</button>' : ''}
    </div>
    <div class="post-body"></div>
    ${post.matchup ? `
      <a class="post-matchup ${post.matchup.overruled ? 'overruled' : ''}" href="${matchupHref(post.matchup)}">
        <div class="post-matchup-names">${escapeHtml(shortName(post.matchup.name_a))} <span>vs</span> ${escapeHtml(shortName(post.matchup.name_b))}</div>
        <div class="post-matchup-verdict">${escapeHtml(post.matchup.verdict)}</div>
      </a>` : ''}
    <div class="post-actions">
      <button class="post-like ${post.liked_by_me ? 'liked' : ''}">♥ <span>${post.like_count}</span></button>
      ${isReply ? '' : `<button class="post-replies">Replies (<span>${post.reply_count}</span>)</button>`}
    </div>
    <div class="post-thread" style="display:none;"></div>`;
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

  const del = el.querySelector('.post-delete');
  if (del) {
    del.addEventListener('click', async () => {
      if (!confirm(isReply ? 'Delete this reply?' : 'Delete this post and all its replies?')) return;
      try { await Api.deletePost(post.id); el.remove(); } catch (err) { alert(err.message); }
    });
  }

  const repliesBtn = el.querySelector('.post-replies');
  if (repliesBtn) {
    const thread = el.querySelector('.post-thread');
    repliesBtn.addEventListener('click', async () => {
      if (thread.style.display !== 'none') { thread.style.display = 'none'; return; }
      thread.style.display = '';
      thread.innerHTML = '<div class="char-stat-raw">Loading…</div>';
      try {
        const t = await Api.getThread(post.id);
        thread.innerHTML = '';
        const list = document.createElement('div');
        t.replies.forEach((r) => list.appendChild(postEl(r, { isReply: true })));
        thread.appendChild(list);
        thread.appendChild(composerEl({
          parentId: post.id,
          placeholder: 'Reply…',
          onPosted: (reply) => {
            list.appendChild(postEl(reply, { isReply: true }));
            const n = repliesBtn.querySelector('span');
            n.textContent = Number(n.textContent) + 1;
          },
        }));
      } catch (err) {
        thread.innerHTML = `<div class="add-character-status error">${escapeHtml(err.message)}</div>`;
      }
    });
  }
  return el;
}

async function loadPage() {
  loadMore.disabled = true;
  try {
    const res = await Api.listPosts(nextBefore);
    if (!res.posts.length && !nextBefore) {
      feed.innerHTML = '<div class="empty-state">No posts yet — share a matchup from any comparison page.</div>';
    }
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
  document.getElementById('composer-slot').appendChild(composerEl({
    onPosted: (post) => {
      const empty = feed.querySelector('.empty-state');
      if (empty) empty.remove();
      feed.prepend(postEl(post));
    },
  }));
  loadPage();
})();
