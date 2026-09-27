// Admins' ticket queue: open tickets (reply, overrule in the writer's
// favor, or ban them from tickets), answered ones, and the ban list.
// The server checks admin rights on every call; this page just hides.

renderTopbar([]);

const body = document.getElementById('tk-body');
const openN = document.getElementById('tk-open-n');
let tab = 'open';
const setOpen = (n) => { openN.textContent = n ? `(${n})` : ''; };

// "5m ago", "just now", or a date for older ones.
function ago(iso) {
  const t = timeAgo(iso);
  return t === 'now' ? 'just now' : /^\d+[mhd]$/.test(t) ? `${t} ago` : t;
}

function matchupHref(m) {
  const p = new URLSearchParams({ a: m.char_a, b: m.char_b, fa: m.form_a, fb: m.form_b });
  return `compare.html?${p}`;
}

function verdictLine(m) {
  if (!m) return 'A character in this matchup was removed.';
  const forms = m.form_a === 'Base' && m.form_b === 'Base' ? '' : `${escapeHtml(m.form_a)} · ${escapeHtml(m.form_b)} · `;
  return forms + (m.overruled_winner ? `Currently overruled: ${escapeHtml(m.overruled_winner)} wins` : `Current verdict: ${escapeHtml(m.calc_verdict)}`);
}

function titleHtml(m, cls) {
  if (!m) return `<span class="${cls}">Removed matchup</span>`;
  return `<a class="${cls}" href="${matchupHref(m)}">${escapeHtml(m.label_a)} vs ${escapeHtml(m.label_b)}</a>`;
}

const authorLink = (t) => `<a class="tk-author" href="user.html?u=${encodeURIComponent(t.author)}">${escapeHtml(t.author)}</a>`;

// An open ticket: the matchup, who wrote it and what they say, their
// reason, and the three ways to answer.
function openCard(t) {
  const el = document.createElement('article');
  el.className = 'tk-card';
  const m = t.matchup;
  el.innerHTML = `
    <div class="tk-head">
      <div>${titleHtml(m, 'tk-title')}<div class="tk-verdict">${verdictLine(m)}</div></div>
      <span class="tk-time">${ago(t.created_at)}</span>
    </div>
    <div class="tk-who${t.author_banned ? ' banned' : ''}">
      ${userAvatarHtml(t.author, t.author_avatar, 'mc-avatar')}${authorLink(t)}
      ${t.author_banned ? '<span class="tk-banned">banned</span>' : ''}
      <span class="tk-says">says ${escapeHtml(t.winner)} wins</span>
    </div>
    <div class="tk-reason"></div>
    <textarea class="tk-reply" rows="2" maxlength="1000" placeholder="Optional reply… (an overrule without one uses their reason)"></textarea>
    <div class="tk-actions">
      <button type="button" class="tk-btn" data-act="answer">Reply &amp; keep verdict</button>
      ${m ? `<button type="button" class="tk-btn gold" data-act="overrule">Overrule: ${escapeHtml(t.winner)} wins</button>` : ''}
      ${t.author_banned ? '' : '<button type="button" class="tk-btn danger" data-act="ban">Ban from tickets</button>'}
    </div>
    <div class="form-error tk-error"></div>`;
  el.querySelector('.tk-reason').textContent = `“${t.reason}”`;
  el.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const err = el.querySelector('.tk-error');
    const text = el.querySelector('.tk-reply').value.trim();
    try {
      if (btn.dataset.act === 'ban') {
        const reason = prompt(`Ban ${t.author} from sending tickets? Reason (they'll see it):`, 'Spamming tickets');
        if (reason === null) return;
        btn.disabled = true;
        await Api.banFromTickets(t.author, reason);
      } else if (btn.dataset.act === 'answer') {
        btn.disabled = true;
        await Api.answerTicket(t.id, text);
      } else if (btn.dataset.act === 'overrule') {
        if (!confirm(`Overrule ${m.label_a} vs ${m.label_b} (${m.form_a} · ${m.form_b}): ${t.winner} wins? It's posted to the Board, crediting ${t.author}.`)) return;
        btn.disabled = true;
        await Api.overruleTicket(t.id, text);
      }
      load();
    } catch (ex) {
      err.textContent = ex.message;
      btn.disabled = false;
    }
  });
  return el;
}

// An answered ticket: one compact row, with the reply under it.
function answeredRow(t) {
  const el = document.createElement('article');
  el.className = 'tk-row';
  el.innerHTML = `
    <div class="tk-row-main">
      ${titleHtml(t.matchup, 'tk-row-title')}
      <div class="tk-verdict">${authorLink(t)} said ${escapeHtml(t.winner)} wins · answered by ${escapeHtml(t.admin || 'an admin')}${t.answered_at ? ` · ${ago(t.answered_at)}` : ''}</div>
      ${t.response ? '<div class="tk-row-reply"></div>' : ''}
    </div>
    ${t.outcome === 'overruled' ? '<span class="tk-pill gold">Overruled</span>' : '<span class="tk-pill">Kept</span>'}`;
  const reply = el.querySelector('.tk-row-reply');
  if (reply) reply.textContent = `“${t.response}”`;
  return el;
}

async function loadBans() {
  const bans = await Api.ticketBans();
  body.innerHTML = `
    <form class="tk-row tk-ban-form">
      <div class="tk-ban-title">Add a ban</div>
      <div class="tk-ban-fields">
        <input class="duel-input" name="u" placeholder="Username" maxlength="20" autocomplete="off" spellcheck="false" required>
        <input class="duel-input" name="r" placeholder="Reason (they'll see it)" maxlength="300">
        <button class="tk-btn gold">Ban</button>
      </div>
      <div class="form-error tk-error"></div>
    </form>
    ${bans.length ? '' : '<div class="duel-note">Nobody is banned from tickets.</div>'}
    ${bans.map((b) => `
      <div class="tk-row">
        <div class="tk-row-main"><a class="tk-author" href="user.html?u=${encodeURIComponent(b.username)}">${escapeHtml(b.username)}</a>
          <span class="tk-verdict">${escapeHtml(b.reason || 'No reason given')} · banned by ${escapeHtml(b.admin)} · ${ago(b.created_at)}</span></div>
        <button type="button" class="tk-btn" data-unban="${escapeHtml(b.username)}">Unban</button>
      </div>`).join('')}`;
  const form = body.querySelector('form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await Api.banFromTickets(form.u.value.trim(), form.r.value.trim());
      loadBans();
    } catch (ex) { form.querySelector('.tk-error').textContent = ex.message; }
  });
  body.querySelectorAll('[data-unban]').forEach((btn) => btn.addEventListener('click', async () => {
    btn.disabled = true;
    try { await Api.unbanFromTickets(btn.dataset.unban); loadBans(); } catch (ex) { alert(ex.message); btn.disabled = false; }
  }));
}

async function load() {
  body.innerHTML = '<div class="duel-note">Loading…</div>';
  try {
    if (tab === 'bans') {
      await loadBans();
      setOpen((await Api.adminTicketCount()).open);
      return;
    }
    const res = await Api.adminTickets(tab);
    setOpen(res.open_count);
    body.innerHTML = '';
    if (!res.tickets.length) {
      body.innerHTML = `<div class="duel-card duel-empty"><div class="duel-empty-title">${tab === 'open' ? 'No open tickets' : 'Nothing answered yet'}</div></div>`;
      return;
    }
    res.tickets.forEach((t) => body.appendChild(tab === 'open' ? openCard(t) : answeredRow(t)));
  } catch (ex) {
    body.innerHTML = `<div class="error-state">${escapeHtml(ex.message)}</div>`;
  }
}

document.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
  tab = b.dataset.tab;
  document.querySelectorAll('[data-tab]').forEach((x) => {
    x.classList.toggle('active', x === b);
    x.setAttribute('aria-selected', String(x === b));
  });
  load();
}));

(async () => {
  const me = await currentUser();
  if (!me || !me.is_admin) {
    body.innerHTML = '<div class="duel-card duel-empty"><div class="duel-empty-title">Admins only</div><p class="duel-note">This page is where admins answer tickets.</p></div>';
    return;
  }
  load();
})();
