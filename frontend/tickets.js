// Admins' ticket queue: open tickets (reply, overrule in the writer's
// favor, or ban them from tickets), answered ones, and the ban list.
// The server checks admin rights on every call; this page just hides.

renderTopbar([]);

const body = document.getElementById('tk-body');
const openN = document.getElementById('tk-open-n');
let tab = 'open';

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
  return m.overruled_winner ? `Currently overruled: ${escapeHtml(m.overruled_winner)} wins` : `Calculator: ${escapeHtml(m.calc_verdict)}`;
}

function ticketCard(t) {
  const el = document.createElement('article');
  el.className = 'tk-card';
  const m = t.matchup;
  const title = m ? `${escapeHtml(m.label_a)} <span class="duel-vs">vs</span> ${escapeHtml(m.label_b)}` : 'Removed matchup';
  const forms = m ? `<span class="tk-forms">${escapeHtml(m.form_a)} · ${escapeHtml(m.form_b)}</span>` : '';
  const who = `${userAvatarHtml(t.author, t.author_avatar, 'mc-avatar')}
    <div><a class="tk-author" href="user.html?u=${encodeURIComponent(t.author)}">${escapeHtml(t.author)}</a>${t.author_banned ? ' <span class="ticket-status kept">banned</span>' : ''}
    <div class="tk-when">${ago(t.created_at)} · says <b>${escapeHtml(t.winner)}</b> wins</div></div>`;
  let foot;
  if (t.status === 'open') {
    foot = `
      <textarea class="tk-reply" rows="2" maxlength="1000" placeholder="Reply (optional for an overrule: their reason is used)"></textarea>
      <div class="tk-actions">
        <span class="form-error tk-error"></span>
        ${t.author_banned ? '' : '<button type="button" class="pill-button" data-act="ban">Ban from tickets</button>'}
        <button type="button" class="pill-button" data-act="answer">Reply &amp; keep verdict</button>
        ${m ? `<button type="button" class="btn-gold" data-act="overrule">Overrule: ${escapeHtml(t.winner)} wins</button>` : ''}
      </div>`;
  } else {
    const how = t.outcome === 'overruled' ? '<span class="ticket-status overruled">Overruled</span>' : '<span class="ticket-status kept">Kept</span>';
    foot = `<div class="ticket-line">${how} by ${escapeHtml(t.admin || 'an admin')}${t.answered_at ? ` · ${ago(t.answered_at)}` : ''}</div>
      ${t.response ? '<div class="ticket-reply"></div>' : ''}`;
  }
  el.innerHTML = `
    <div class="tk-head">
      <div>${m ? `<a class="tk-title" href="${matchupHref(m)}">${title}</a>` : `<span class="tk-title">${title}</span>`} ${forms}
        <div class="tk-verdict">${verdictLine(m)}</div></div>
    </div>
    <div class="tk-who">${who}</div>
    <div class="tk-reason"></div>
    ${foot}`;
  el.querySelector('.tk-reason').textContent = t.reason;
  const reply = el.querySelector('.ticket-reply');
  if (reply) reply.textContent = t.response;
  el.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const err = el.querySelector('.tk-error');
    const text = el.querySelector('.tk-reply')?.value.trim() || '';
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
      if (err) err.textContent = ex.message;
      btn.disabled = false;
    }
  });
  return el;
}

async function loadBans() {
  const bans = await Api.ticketBans();
  body.innerHTML = `
    <form class="tk-ban-form">
      <input class="duel-input" name="u" placeholder="Username" maxlength="20" autocomplete="off" spellcheck="false">
      <input class="duel-input" name="r" placeholder="Reason (they'll see it)" maxlength="300">
      <button class="btn-gold">Ban from tickets</button>
      <span class="form-error tk-error"></span>
    </form>
    ${bans.length ? '' : '<div class="duel-note">Nobody is banned from tickets.</div>'}
    <div class="tk-bans">${bans.map((b) => `
      <div class="tk-ban"><div><b>${escapeHtml(b.username)}</b> <span class="tk-when">by ${escapeHtml(b.admin)} · ${ago(b.created_at)}</span>
        <div class="tk-when">${escapeHtml(b.reason || 'No reason given')}</div></div>
        <button type="button" class="pill-button" data-unban="${escapeHtml(b.username)}">Unban</button></div>`).join('')}</div>`;
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
      openN.textContent = (await Api.adminTicketCount()).open || '';
      return;
    }
    const res = await Api.adminTickets(tab);
    openN.textContent = res.open_count || '';
    body.innerHTML = '';
    if (!res.tickets.length) {
      body.innerHTML = `<div class="duel-card duel-empty"><div class="duel-empty-title">${tab === 'open' ? 'No open tickets' : 'Nothing answered yet'}</div></div>`;
      return;
    }
    res.tickets.forEach((t) => body.appendChild(ticketCard(t)));
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
