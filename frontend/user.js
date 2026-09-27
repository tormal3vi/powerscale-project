// A user's public profile (user.html?u=NAME): who they are, their numbers,
// and their latest duels, posts, matchup comments and the overrules their
// tickets led to. Admins also see their tickets and can ban them from
// tickets.

renderTopbar([], { back: true });

const root = document.getElementById('up');
const username = new URLSearchParams(location.search).get('u') || '';

// "5m ago", "just now", or a date for older ones.
function ago(iso) {
  const t = timeAgo(iso);
  return t === 'now' ? 'just now' : /^\d+[mhd]$/.test(t) ? `${t} ago` : t;
}

// User-written text goes in with textContent after the page is built:
// placeholders carry an index into this list.
let texts = [];
const textSlot = (text) => `<span class="up-body" data-t="${texts.push(text) - 1}"></span>`;

function stat(value, label) {
  return `<div class="up-stat"><div class="up-stat-value">${value}</div><div class="up-stat-label">${label}</div></div>`;
}

function section(title, inner, empty) {
  return `<section class="up-section"><h2 class="duel-section-title">${title}</h2>${inner || `<div class="duel-note">${empty}</div>`}</section>`;
}

function duelLine(g) {
  const word = { win: 'Won', loss: 'Lost', draw: 'Draw' }[g.outcome] || 'Finished';
  const me = g.players.find((p) => p.me);
  const rivals = g.players.filter((p) => !me || p.team !== me.team).map((p) => escapeHtml(p.username)).join(', ');
  const score = g.teams === 2 ? (g.my_team === 2 ? `${g.team_scores[1]}–${g.team_scores[0]}` : g.team_scores.join('–')) : g.team_scores.join(' · ');
  return `<a class="up-row" href="duels.html?game=${g.id}">
    <span class="duel-tag">${g.format}</span>
    <span class="up-row-main"><span class="duel-outcome ${g.outcome || ''}">${word} ${score}</span> <span class="up-muted">vs ${rivals}</span></span>
  </a>`;
}

function postLine(p) {
  const m = p.matchup;
  return `<a class="up-row up-post" href="board.html">
    <span class="up-row-main">
      ${textSlot(p.body)}
      ${m ? `<span class="up-muted up-mu">${escapeHtml(m.label_a)} vs ${escapeHtml(m.label_b)}</span>` : ''}
    </span>
    <span class="up-when">${ago(p.created_at)} · ♥ ${p.like_count} · ${p.reply_count} repl${p.reply_count === 1 ? 'y' : 'ies'}</span>
  </a>`;
}

function commentLine(c) {
  return `<a class="up-row up-post" href="${escapeHtml(c.compare_url)}">
    <span class="up-row-main"><span class="up-muted up-mu">${escapeHtml(c.label_a)} vs ${escapeHtml(c.label_b)}</span>${textSlot(c.body)}</span>
    <span class="up-when">${ago(c.created_at)}</span>
  </a>`;
}

function creditLine(p) {
  const m = p.matchup;
  if (!m) return '';
  return `<a class="up-row" href="compare.html?${new URLSearchParams({ a: m.char_a, b: m.char_b, fa: m.form_a, fb: m.form_b })}">
    <span class="up-row-main"><b>${escapeHtml(m.overruled_winner || (p.ruling && p.ruling.winner) || '')}</b> <span class="up-muted">wins · ${escapeHtml(m.label_a)} vs ${escapeHtml(m.label_b)}</span></span>
    <span class="up-when">${ago(p.created_at)}</span>
  </a>`;
}

function ticketLine(t) {
  const m = t.matchup;
  const status = t.status === 'open' ? '<span class="ticket-status open">Open</span>'
    : t.outcome === 'overruled' ? '<span class="ticket-status overruled">Overruled</span>' : '<span class="ticket-status kept">Kept</span>';
  return `<div class="up-row up-post">
    <span class="up-row-main"><span>${status} <b>${escapeHtml(t.winner)}</b> <span class="up-muted">should win · ${m ? `${escapeHtml(m.label_a)} vs ${escapeHtml(m.label_b)}` : 'removed matchup'}</span></span>
      ${textSlot(t.reason)}</span>
    <span class="up-when">${ago(t.created_at)}</span>
  </div>`;
}

async function render() {
  if (!username) { root.innerHTML = '<div class="error-state">No user given.</div>'; return; }
  let d;
  try {
    d = await Api.userPage(username);
  } catch (e) {
    root.innerHTML = `<div class="error-state">${escapeHtml(e.message)}</div>`;
    return;
  }
  const p = d.profile;
  texts = [];
  const viewer = await currentUser();
  const canBan = viewer && viewer.is_admin && !p.is_admin;
  document.title = `${p.username} — Powerscale`;
  setTopbarTitle(p.username);
  const rec = p.record || { wins: 0, draws: 0, losses: 0 };
  const played = rec.wins + rec.draws + rec.losses;
  const fav = p.favorite;
  root.innerHTML = `
    <header class="up-head">
      ${userAvatarHtml(p.username, p.avatar_url, 'up-avatar', { admin: p.is_admin })}
      <div class="up-id">
        <div class="up-name-row"><h1 class="up-name"></h1>${p.is_admin ? adminBadgeHtml({ solid: true }) : ''}</div>
        <div class="up-since">Member since ${monthYear(p.member_since)}</div>
        ${p.bio ? '<p class="up-bio"></p>' : ''}
        ${fav ? `<a class="up-fav" href="character.html?id=${fav.id}">
          <span class="up-fav-tile${fav.image_url ? ' has-pic' : ''}" style="background:${accentFor(fav.id)}">${characterTileInner(fav.name, fav.image_url, 64)}</span>
          <span><span class="up-muted">Favorite</span> <b>${escapeHtml(fav.name)}</b></span></a>` : ''}
      </div>
    </header>
    <div class="up-stats">
      ${stat(p.post_count, `post${p.post_count === 1 ? '' : 's'}`)}
      ${stat(d.likes_received, `like${d.likes_received === 1 ? '' : 's'} received`)}
      ${stat(d.comment_count, `comment${d.comment_count === 1 ? '' : 's'}`)}
      ${stat(played ? `${rec.wins}–${rec.draws}–${rec.losses}` : '—', `duels${d.duel_rank ? ` · #${d.duel_rank}` : ''}`)}
      ${stat(d.overrules_suggested, `overrule${d.overrules_suggested === 1 ? '' : 's'} suggested`)}
    </div>
    <div class="up-grid">
      <div class="up-col">
        ${section('Recent duels', d.duels.map(duelLine).join(''), 'No finished duels yet.')}
        ${section('Overrules they suggested', d.credited.map(creditLine).join(''), 'None yet. A ticket that leads to an overrule shows up here.')}
      </div>
      <div class="up-col">
        ${section('Posts', d.posts.map(postLine).join(''), 'No posts yet.')}
        ${section('Matchup comments', d.comments.map(commentLine).join(''), 'No comments yet.')}
        ${canBan ? section('Tickets <span class="up-admin-only">admins only</span>',
          `<div class="up-ban">${d.ticket_banned
            ? `Banned from tickets${d.ticket_ban_reason ? `: ${escapeHtml(d.ticket_ban_reason)}` : ''} <button type="button" class="pill-button btn-sm" data-act="unban">Unban</button>`
            : `Can send tickets <button type="button" class="pill-button btn-sm" data-act="ban">Ban from tickets</button>`}</div>
           ${d.tickets.map(ticketLine).join('') || '<div class="duel-note">No tickets.</div>'}`, '') : ''}
      </div>
    </div>`;
  root.querySelector('.up-name').textContent = p.username;
  const bio = root.querySelector('.up-bio');
  if (bio) bio.textContent = p.bio;
  root.querySelectorAll('[data-t]').forEach((el) => { el.textContent = texts[Number(el.dataset.t)]; });
  root.querySelectorAll('[data-act]').forEach((btn) => btn.addEventListener('click', async () => {
    try {
      if (btn.dataset.act === 'ban') {
        const reason = prompt(`Ban ${p.username} from sending tickets? Reason (they'll see it):`, 'Spamming tickets');
        if (reason === null) return;
        await Api.banFromTickets(p.username, reason);
      } else {
        await Api.unbanFromTickets(p.username);
      }
      render();
    } catch (e) { alert(e.message); }
  }));
}

render();
