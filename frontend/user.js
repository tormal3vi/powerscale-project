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
const textSlot = (text, cls = 'up-body') => `<span class="${cls}" data-t="${texts.push(text) - 1}"></span>`;
const SHIELD_ICON = '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.2 14 3.8v3.6c0 3.7-2.5 6.1-6 7.4-3.5-1.3-6-3.7-6-7.4V3.8L8 1.2Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>';

function stat(value, label, cls = '') {
  return `<div class="up-stat ${cls}"><div class="up-stat-value">${value}</div><div class="up-stat-label">${label}</div></div>`;
}

// A list card: title, then rows separated by thin lines.
function card(title, rows, empty) {
  return `<section class="up-card"><h2 class="up-card-title">${title}</h2>
    ${rows ? `<div class="up-list">${rows}</div>` : `<div class="up-empty-line">${empty}</div>`}</section>`;
}

function duelLine(g) {
  const word = { win: 'Won', loss: 'Lost', draw: 'Draw' }[g.outcome] || 'Finished';
  const me = g.players.find((p) => p.me);
  const rivals = g.players.filter((p) => !me || p.team !== me.team).map((p) => escapeHtml(p.username)).join(', ');
  const score = g.teams === 2 ? (g.my_team === 2 ? `${g.team_scores[1]}–${g.team_scores[0]}` : g.team_scores.join('–')) : g.team_scores.join(' · ');
  return `<a class="up-item" href="duels.html?game=${g.id}">
    <span class="up-item-main"><span class="up-tag">${g.mode === 'draft' ? 'Draft · ' : ''}${g.format}</span>vs ${rivals}</span>
    <span class="up-result ${g.outcome || ''}">${word} ${score}</span>
  </a>`;
}

function postLine(p) {
  const m = p.matchup;
  return `<a class="up-item up-stack" href="board.html">
    ${textSlot(p.body, 'up-text')}
    <span class="up-meta">♥ ${p.like_count} · ${p.reply_count} repl${p.reply_count === 1 ? 'y' : 'ies'}${m ? ` · ${escapeHtml(m.label_a)} vs ${escapeHtml(m.label_b)}` : ''} · ${ago(p.created_at)}</span>
  </a>`;
}

function commentLine(c) {
  return `<a class="up-item up-stack" href="${escapeHtml(c.compare_url)}">
    <span class="up-meta">On ${escapeHtml(c.label_a)} vs ${escapeHtml(c.label_b)} · ${ago(c.created_at)}</span>
    ${textSlot(`“${c.body}”`, 'up-text')}
  </a>`;
}

function creditLine(p) {
  const m = p.matchup;
  if (!m) return '';
  const winner = m.overruled_winner || (p.ruling && p.ruling.winner) || '';
  return `<a class="up-item" href="compare.html?${new URLSearchParams({ a: m.char_a, b: m.char_b, fa: m.form_a, fb: m.form_b })}">
    <span class="up-item-main">${escapeHtml(m.label_a)} vs ${escapeHtml(m.label_b)}</span>
    ${winner ? `<span class="up-win-pill">${escapeHtml(winner)} wins</span>` : ''}
  </a>`;
}

function ticketLine(t) {
  const m = t.matchup;
  const status = t.status === 'open' ? '<span class="ticket-status open">Open</span>'
    : t.outcome === 'overruled' ? '<span class="ticket-status overruled">Overruled</span>' : '<span class="ticket-status kept">Kept</span>';
  return `<div class="up-item up-stack">
    <span class="up-item-row"><span class="up-item-main">${m ? `${escapeHtml(m.label_a)} vs ${escapeHtml(m.label_b)}` : 'Removed matchup'}
      <span class="up-meta">· says ${escapeHtml(t.winner)} wins · ${ago(t.created_at)}</span></span>${status}</span>
    ${textSlot(t.reason, 'up-text up-muted')}
  </div>`;
}

// Every title earned, and the next ones to work towards.
function titlesCardHtml(p) {
  if (!p.titles.length && !p.next_titles.length) return '';
  const next = p.next_titles.map((t) => `
    <div class="up-next t-${escapeHtml(t.color)}">
      <div class="up-next-head"><span class="up-next-name">${escapeHtml(t.name)}</span>
        <span class="up-next-count">${t.have.toLocaleString('en')} / ${t.need.toLocaleString('en')} ${escapeHtml(t.what)}</span></div>
      <div class="up-next-bar"><span style="width:${Math.min(100, Math.round((100 * t.have) / t.need))}%"></span></div>
    </div>`).join('');
  return `<section class="up-card up-titles">
    <h2 class="up-card-title">Titles</h2>
    ${p.titles.length ? `<div class="up-sublbl">Earned</div><div class="up-title-list">${p.titles.map((t) => titleHtml(t)).join('')}</div>`
      : '<div class="up-empty-line" style="margin-bottom:18px">None yet: win duels to earn the first.</div>'}
    ${next ? `<div class="up-sublbl">Next up</div><div class="up-next-list">${next}</div>` : ''}
  </section>`;
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
  const isMe = viewer && viewer.username.toLowerCase() === p.username.toLowerCase();
  document.title = `${p.username} — Powerscale`;
  setTopbarTitle(p.username);
  const rec = p.record || { wins: 0, draws: 0, losses: 0 };
  const played = rec.wins + rec.draws + rec.losses;
  const fav = p.favorite;
  const nothing = !d.duels.length && !d.posts.length && !d.comments.length && !d.credited.length;
  root.innerHTML = `
    <header class="up-head">
      ${userAvatarHtml(p.username, p.avatar_url, 'up-avatar', { admin: p.is_admin })}
      <div class="up-id">
        <div class="up-name-row"><h1 class="up-name"></h1>${p.is_admin ? adminBadgeHtml({ solid: true }) : ''}${titleHtml(p.title, 'utitle-lg')}</div>
        ${p.bio ? '<p class="up-bio"></p>' : ''}
        <div class="up-line">
          ${fav ? `<a class="up-fav" href="character.html?id=${fav.id}">
            <span class="up-fav-tile${fav.image_url ? ' has-pic' : ''}" style="background:${accentFor(fav.id)}">${characterTileInner(fav.name, fav.image_url, 48)}</span>
            ${escapeHtml(fav.name)}</a>` : ''}
          ${p.discord ? `<a class="up-discord" href="https://discord.com/users/${encodeURIComponent(p.discord_id)}" target="_blank"
            rel="noopener" title="Their Discord account">${DISCORD_ICON}<span class="up-discord-name"></span></a>` : ''}
          <span class="up-since">Member since ${monthYear(p.member_since)}${p.bio ? '' : ' · no bio yet'}</span>
        </div>
      </div>
      ${isMe || !p.challenge_button ? '' : `<a class="btn-gold up-challenge" href="duels.html?invite=${encodeURIComponent(p.username)}"
        title="Invite them to a duel"><span aria-hidden="true">⚔️</span>Challenge</a>`}
    </header>
    <div class="up-stats">
      ${stat(p.post_count, 'Posts')}
      ${stat(d.likes_received, 'Likes received')}
      ${stat(d.comment_count, 'Matchup comments')}
      ${d.duel_rank ? stat(`#${d.duel_rank}`, `${rec.wins}–${rec.draws}–${rec.losses} duel record`, 'rank')
        : stat(played ? `${rec.wins}–${rec.draws}–${rec.losses}` : '0–0–0', 'Duel record')}
      ${stat(d.overrules_suggested, 'Overrules suggested')}
    </div>
    ${titlesCardHtml(p)}
    ${nothing ? '<div class="up-card up-nothing">No duels, posts or comments yet.</div>' : `
    <div class="up-grid">
      <div class="up-col">
        ${card('Recent duels', d.duels.map(duelLine).join(''), 'No finished duels yet.')}
        ${card('Recent matchup comments', d.comments.map(commentLine).join(''), 'No comments yet.')}
      </div>
      <div class="up-col">
        ${card('Recent Board posts', d.posts.map(postLine).join(''), 'No posts yet.')}
        ${card('Overrules suggested', d.credited.map(creditLine).join(''), 'None yet. A ticket that leads to an overrule shows up here.')}
      </div>
    </div>`}
    ${canBan ? `<section class="up-card up-admin">
      <h2 class="up-card-title up-admin-title">${SHIELD_ICON} Admin-only · tickets sent by this user</h2>
      ${d.tickets.length ? `<div class="up-list">${d.tickets.map(ticketLine).join('')}</div>` : '<div class="up-empty-line">No tickets.</div>'}
      <div class="up-ban">
        ${d.ticket_banned
          ? `<button type="button" class="pill-button btn-sm" data-act="unban">Unban</button><span class="up-meta">Banned from tickets${d.ticket_ban_reason ? `: ${escapeHtml(d.ticket_ban_reason)}` : ''}</span>`
          : '<button type="button" class="up-ban-btn" data-act="ban">Ban from tickets</button><span class="up-meta">No active ban</span>'}
      </div>
    </section>` : ''}`;
  root.querySelector('.up-name').textContent = p.username;
  const bio = root.querySelector('.up-bio');
  if (bio) bio.textContent = p.bio;
  const discordName = root.querySelector('.up-discord-name');
  if (discordName) discordName.textContent = p.discord;
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
