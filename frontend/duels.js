// Prediction duels: make a game (1v1, free-for-all or teams), see your
// games and the leaderboard, and play rounds against the clock. Rules live
// on the server (backend/duels.py) - its clock is the one that counts; the
// one drawn here only shows it.

renderTopbar([]);

const newBox = document.getElementById('duel-new');
const listsBox = document.getElementById('duel-lists');
const boardBox = document.getElementById('leaderboard');
const playBox = document.getElementById('duel-play');
const FORMATS = ['1v1', '1v1v1', '1v1v1v1', '2v2', '2v2v2', '3v3'];
let me = null;
let games = { mine: [], open: [] };
const expanded = new Set(); // finished games whose rounds are shown

// --- small pieces ----------------------------------------------------------------

const avatarHtml = (p, cls = 'duel-avatar') => userAvatarHtml(p.username, p.avatar_url, cls, { admin: p.is_admin });
const isTeamGame = (g) => g.team_size > 1;
const teamName = (g, t) => (isTeamGame(g) ? `Team ${t}` : `Seat ${t}`);
const names = (list) => list.map((p) => escapeHtml(p.username)).join(', ');

// "vs duel_bob" / "with alice · vs bob, carl" / "vs bob, carl" for the
// player looking at it; "alice's 2v2" for someone else's open game.
function gameTitle(g) {
  const mine = g.players.find((p) => p.me);
  if (!mine && !g.invited.includes(me?.username)) return `${escapeHtml(g.creator)}'s ${g.format}`;
  const team = mine ? mine.team : null;
  const mates = g.players.filter((p) => !p.me && team && p.team === team);
  const rivals = g.players.filter((p) => !p.me && p.team !== team);
  const parts = [];
  if (mates.length) parts.push(`with ${names(mates)}`);
  if (rivals.length) parts.push(`vs ${names(rivals)}`);
  return parts.join(' · ') || (g.private ? `vs ${g.invited.map(escapeHtml).join(', ')}` : 'Open game');
}

function sideTileHtml(side, px) {
  // Eager, high priority: the round's clock is already running.
  const pic = characterPictureUrl(side.image_url, px);
  return `<span class="duel-tile${pic ? ' has-pic' : ''}" style="background:${accentFor(side.id)}">
    <span class="tile-initial">${escapeHtml(initialFor(side.name))}</span>${pic
      ? `<img src="${escapeHtml(pic)}" alt="" fetchpriority="high" referrerpolicy="no-referrer" onerror="this.parentNode.classList.remove('has-pic');this.remove()">`
      : ''}</span>`;
}

function sideLabel(side) {
  return `${escapeHtml(bareName(side.name))}${side.form ? ` <span class="duel-form">${escapeHtml(side.form)}</span>` : ''}`;
}

function gameLink(id) {
  return `${location.origin}${location.pathname.replace(/[^/]*$/, '')}duels.html?game=${id}`;
}

// --- new game ------------------------------------------------------------------------

function renderNew() {
  if (!me) {
    const next = encodeURIComponent('duels.html' + location.search);
    newBox.innerHTML = `
      <h2 class="duel-card-title" id="new-title">Start a duel</h2>
      <p class="duel-note"><a href="login.html?next=${next}">Log in</a> or <a href="login.html?mode=register&next=${next}">create an account</a> to play.</p>`;
    return;
  }
  newBox.innerHTML = `
    <h2 class="duel-card-title" id="new-title">New game</h2>
    <div class="duel-field">
      <span class="lbl">Format</span>
      <div class="seg seg-wrap" role="radiogroup" aria-label="Format">
        ${FORMATS.map((f, i) => `<button type="button" class="seg-btn${i ? '' : ' active'}" data-fmt="${f}" role="radio" aria-checked="${!i}">${f}</button>`).join('')}
      </div>
    </div>
    <div class="duel-field">
      <span class="lbl">Players</span>
      <div class="seg" role="radiogroup" aria-label="Players">
        <button type="button" class="seg-btn active" data-opp="anyone" role="radio" aria-checked="true">Open to anyone</button>
        <button type="button" class="seg-btn" data-opp="invite" role="radio" aria-checked="false">Invite players</button>
      </div>
      <div class="duel-invites" hidden></div>
    </div>
    <div class="duel-field">
      <span class="lbl">Matchups</span>
      <div class="seg" role="radiogroup" aria-label="Matchups">
        <button type="button" class="seg-btn active" data-mu="random" role="radio" aria-checked="true">Random</button>
        <button type="button" class="seg-btn" data-mu="pick" role="radio" aria-checked="false">Pick my own</button>
      </div>
      <div class="duel-pickers" hidden></div>
      <button type="button" class="duel-add" hidden>+ Add a matchup</button>
      <p class="duel-hint" id="duel-mu-hint"></p>
    </div>
    <div class="duel-actions">
      <span class="form-error" id="duel-error"></span>
      <button type="button" class="btn-gold" id="duel-create">Create game</button>
    </div>`;

  const invitesBox = newBox.querySelector('.duel-invites');
  const pickers = newBox.querySelector('.duel-pickers');
  const addBtn = newBox.querySelector('.duel-add');
  const hint = newBox.querySelector('#duel-mu-hint');
  const err = newBox.querySelector('#duel-error');
  const picked = new Map(); // picker element -> matchup or null
  let format = '1v1';
  let inviting = false;
  let mode = 'random';

  const setSeg = (attr, value) => newBox.querySelectorAll(`[data-${attr}]`).forEach((b) => {
    const on = b.dataset[attr] === value;
    b.classList.toggle('active', on);
    b.setAttribute('aria-checked', String(on));
  });
  const seats = () => format.split('v').reduce((n, s) => n + Number(s), 0) - 1;
  // One username box per seat to fill, keeping whatever was typed.
  const drawInvites = () => {
    invitesBox.hidden = !inviting;
    const typed = [...invitesBox.querySelectorAll('input')].map((i) => i.value);
    invitesBox.innerHTML = Array.from({ length: seats() }, (_, i) => `
      <input type="text" class="duel-input" placeholder="Username ${seats() > 1 ? i + 1 : ''}" maxlength="20"
        autocomplete="off" spellcheck="false" aria-label="Invited player ${i + 1}" value="${escapeHtml(typed[i] || '')}">`).join('');
  };
  newBox.querySelectorAll('[data-fmt]').forEach((b) => b.addEventListener('click', () => {
    format = b.dataset.fmt;
    setSeg('fmt', format);
    drawInvites();
  }));
  newBox.querySelectorAll('[data-opp]').forEach((b) => b.addEventListener('click', () => {
    inviting = b.dataset.opp === 'invite';
    setSeg('opp', b.dataset.opp);
    drawInvites();
    if (inviting) invitesBox.querySelector('input')?.focus();
  }));

  const updateHint = () => {
    const n = picked.size;
    addBtn.hidden = mode !== 'pick' || n >= 5;
    hint.textContent = mode === 'random'
      ? 'Five matchups drawn at random from characters of similar tiers.'
      : `${n} picked${n < 5 ? `, ${5 - n} drawn at random` : ''}. Only clear wins count: "too close to call" matchups can't be used. You'll know the ones you pick; the others see each only when its 20 seconds start.`;
  };
  const addPicker = () => {
    const el = matchupPickerEl({
      verdict: false,
      onChange: (m) => picked.set(el, m),
      onClose: () => { picked.delete(el); el.remove(); updateHint(); },
    });
    picked.set(el, null);
    pickers.appendChild(el);
    updateHint();
    el.focusFirst();
  };
  newBox.querySelectorAll('[data-mu]').forEach((b) => b.addEventListener('click', () => {
    mode = b.dataset.mu;
    setSeg('mu', mode);
    pickers.hidden = mode !== 'pick';
    if (mode === 'pick' && !picked.size) addPicker();
    updateHint();
  }));
  addBtn.addEventListener('click', addPicker);
  updateHint();

  const createBtn = newBox.querySelector('#duel-create');
  createBtn.addEventListener('click', async () => {
    err.textContent = '';
    const invite = inviting ? [...invitesBox.querySelectorAll('input')].map((i) => i.value.trim()) : [];
    if (invite.some((n) => !n)) { err.textContent = `Fill in all ${seats()} players, or open it to anyone.`; return; }
    const matchups = mode === 'pick' ? [...picked.values()] : [];
    if (matchups.some((m) => !m)) { err.textContent = 'Finish each matchup, or remove it.'; return; }
    createBtn.disabled = true;
    createBtn.textContent = 'Creating…';
    try {
      const g = await Api.createGame(format, invite, matchups);
      renderNew(); // a fresh form
      await refresh();
      showCreated(g);
    } catch (e) {
      err.textContent = e.message;
    } finally {
      createBtn.disabled = false;
      createBtn.textContent = 'Create game';
    }
  });
}

// Right after creating: play now, and the link to send.
function showCreated(g) {
  const box = document.createElement('div');
  box.className = 'duel-created';
  const who = g.private ? `${g.invited.map(escapeHtml).join(', ')} will see it on their Duels page.`
    : 'Anyone can join it from the open games.';
  box.innerHTML = `
    <div class="duel-created-title">${g.format} created</div>
    <div class="duel-note">${who} Play your five rounds whenever you're ready.</div>
    <div class="duel-created-row">
      <button type="button" class="btn-gold" data-act="play">Play now</button>
      <button type="button" class="pill-button" data-act="copy">Copy link</button>
    </div>`;
  box.querySelector('[data-act="play"]').addEventListener('click', () => play(g.id));
  const copy = box.querySelector('[data-act="copy"]');
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(gameLink(g.id)); copy.textContent = 'Link copied'; } catch { copy.textContent = 'Copy failed'; }
    setTimeout(() => { copy.textContent = 'Copy link'; }, 1800);
  });
  newBox.prepend(box);
}

// --- your games ------------------------------------------------------------------------

function scoreLine(g) {
  if (g.teams === 2) return g.my_team === 2 ? `${g.team_scores[1]}–${g.team_scores[0]}` : g.team_scores.join('–');
  return g.team_scores.join(' · ');
}

function statusText(g) {
  switch (g.status) {
    case 'done': {
      const word = { win: 'Won', loss: 'Lost', draw: 'Draw' }[g.outcome] || 'Finished';
      return `<span class="duel-outcome ${g.outcome || ''}">${word}</span> ${scoreLine(g)}`;
    }
    case 'expired': return "Expired: it didn't fill in time";
    case 'declined': return 'Declined by an invited player';
    case 'cancelled': return 'Cancelled';
    default: break;
  }
  if (g.can_decline || (g.can_join && g.private)) return `${escapeHtml(g.creator)} invited you`;
  if (g.can_join) return `${g.seats_left} seat${g.seats_left === 1 ? '' : 's'} left`;
  const waitingFor = g.status === 'open'
    ? (g.invited.length ? `waiting for ${g.invited.map(escapeHtml).join(', ')} to join` : `${g.seats_left} seat${g.seats_left === 1 ? '' : 's'} left`)
    : null;
  if (g.can_play && g.my_played < g.total) return `Your turn · ${g.my_played}/${g.total} played${waitingFor ? ` · ${waitingFor}` : ''}`;
  if (waitingFor) return waitingFor.charAt(0).toUpperCase() + waitingFor.slice(1);
  const done = g.players.filter((p) => p.played >= g.total).length;
  return `Waiting for the others · ${done} of ${g.players.length} done`;
}

function playersHtml(g) {
  const seats = g.teams * g.team_size;
  const shown = g.players.slice(0, 4).map((p) => avatarHtml(p, 'duel-avatar duel-avatar-stack')).join('');
  const empty = Math.min(seats - g.players.length, 4 - Math.min(g.players.length, 4));
  return `<span class="duel-stack">${shown}${'<span class="duel-avatar duel-avatar-stack duel-avatar-open" aria-hidden="true">?</span>'.repeat(Math.max(0, empty))}</span>`;
}

function duelRow(g) {
  const el = document.createElement('div');
  el.className = 'duel-row';
  el.dataset.id = g.id;
  const buttons = [];
  if (g.can_join) {
    if (isTeamGame(g) && g.join_teams.length > 1) {
      g.join_teams.forEach((t) => buttons.push(`<button type="button" class="btn-gold btn-sm" data-act="join" data-team="${t}">Join ${teamName(g, t)}</button>`));
    } else {
      buttons.push('<button type="button" class="btn-gold btn-sm" data-act="join">Join &amp; play</button>');
    }
  }
  if (g.can_decline) buttons.push('<button type="button" class="pill-button btn-sm" data-act="decline">Decline</button>');
  if (g.can_play && g.my_played < g.total) buttons.push(`<button type="button" class="btn-gold btn-sm" data-act="play">${g.my_played ? 'Continue' : 'Play'}</button>`);
  if (g.can_leave) buttons.push('<button type="button" class="pill-button btn-sm" data-act="leave">Leave</button>');
  if (g.can_cancel) buttons.push('<button type="button" class="pill-button btn-sm" data-act="cancel">Cancel</button>');
  if (g.status === 'done') buttons.push(`<button type="button" class="pill-button btn-sm" data-act="results" aria-expanded="${expanded.has(g.id)}">${expanded.has(g.id) ? 'Hide' : 'Results'}</button>`);
  el.innerHTML = `
    <div class="duel-row-main">
      ${playersHtml(g)}
      <div class="duel-row-text">
        <div class="duel-row-title"><span class="duel-format">${g.format}</span>${gameTitle(g)}</div>
        <div class="duel-row-status">${statusText(g)}</div>
      </div>
      <div class="duel-row-buttons">${buttons.join('')}</div>
    </div>
    ${g.status === 'done' && expanded.has(g.id) ? resultsHtml(g) : ''}`;
  el.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-act]');
    if (btn) onRowAction(g, btn.dataset.act, btn);
  });
  return el;
}

// Final standings (teams or seats, best first) and every round with each
// player's pick.
function resultsHtml(g) {
  const teams = Array.from({ length: g.teams }, (_, i) => i + 1)
    .map((t) => ({ t, score: g.team_scores[t - 1], members: g.players.filter((p) => p.team === t) }))
    .sort((a, b) => b.score - a.score);
  const top = teams[0].score;
  const mark = (p) => p.pick_id == null ? '<span class="duel-mark miss" title="No answer">—</span>'
    : `<span class="duel-mark ${p.correct ? 'ok' : 'bad'}" title="${p.correct ? 'Right' : 'Wrong'}">${p.correct ? '✓' : '✗'}</span>`;
  const nameOf = (r, id) => (id == null ? 'no answer' : id === r.a.id ? bareName(r.a.name) : bareName(r.b.name));
  return `
    <div class="duel-standings">
      ${teams.map(({ t, score, members }) => `
        <div class="duel-standing${score === top ? ' top' : ''}${members.some((p) => p.me) ? ' mine' : ''}">
          ${isTeamGame(g) ? `<span class="duel-standing-team">${teamName(g, t)}</span>` : ''}
          <span class="duel-standing-names">${members.map((p) => `${escapeHtml(p.username)}${isTeamGame(g) ? ` <span class="duel-standing-pts">${p.score}</span>` : ''}`).join(', ')}</span>
          <span class="duel-standing-score">${score}</span>
        </div>`).join('')}
    </div>
    <ol class="duel-rounds">
      ${g.rounds.map((r) => `
        <li class="duel-round">
          <div class="duel-round-head">
            <a href="${escapeHtml(r.compare_url)}" class="duel-round-vs">${sideLabel(r.a)} <span class="duel-vs">vs</span> ${sideLabel(r.b)}</a>
            ${r.picked ? '<span class="duel-picked" title="Chosen by the challenger">picked</span>' : ''}
          </div>
          <div class="duel-round-verdict">${escapeHtml(r.verdict)}</div>
          <div class="duel-round-picks">
            ${r.picks.map((p) => `<span>${mark(p)} ${escapeHtml(p.username === me?.username ? 'You' : p.username)}: ${escapeHtml(nameOf(r, p.pick_id))}</span>`).join('')}
          </div>
        </li>`).join('')}
    </ol>`;
}

async function onRowAction(g, act, btn) {
  try {
    if (act === 'play') return play(g.id);
    if (act === 'results') {
      if (expanded.has(g.id)) expanded.delete(g.id); else expanded.add(g.id);
      return renderLists();
    }
    const ask = { decline: 'Decline this invite?', cancel: 'Cancel this game?', leave: 'Leave this game?' }[act];
    if (ask && !confirm(ask)) return;
    btn.disabled = true;
    if (act === 'join') { await Api.joinGame(g.id, Number(btn.dataset.team) || null); await refresh(); return play(g.id); }
    if (act === 'decline') await Api.declineGame(g.id);
    if (act === 'cancel') await Api.cancelGame(g.id);
    if (act === 'leave') await Api.leaveGame(g.id);
    await refresh();
  } catch (e) {
    alert(e.message);
    refresh();
  }
}

function section(title, list, empty) {
  if (!list.length && !empty) return null;
  const el = document.createElement('section');
  el.className = 'duel-section';
  el.innerHTML = `<h2 class="duel-section-title">${title}${list.length ? ` <span class="duel-count">${list.length}</span>` : ''}</h2>`;
  if (!list.length) el.insertAdjacentHTML('beforeend', `<div class="duel-note">${empty}</div>`);
  list.forEach((g) => el.appendChild(duelRow(g)));
  return el;
}

function renderLists() {
  listsBox.innerHTML = '';
  if (!me) return;
  const live = (g) => g.status === 'open' || g.status === 'active';
  const myTurn = (g) => g.can_play && g.my_played < g.total;
  const forYou = games.mine.filter((g) => live(g) && (g.can_join || g.can_decline));
  const yourTurn = games.mine.filter((g) => live(g) && !forYou.includes(g) && myTurn(g));
  const waiting = games.mine.filter((g) => live(g) && !forYou.includes(g) && !myTurn(g));
  const finished = games.mine.filter((g) => !live(g)).slice(0, 15);
  [
    section('Invites for you', forYou),
    section('Your turn', yourTurn, forYou.length ? null : 'Nothing to play right now. Start a game, or join an open one.'),
    section('Waiting', waiting),
    section('Open games', games.open, 'No open games from others right now.'),
    section('Finished', finished),
  ].filter(Boolean).forEach((s) => listsBox.appendChild(s));
}

let finishedIds = null; // for "your game just finished" toasts

async function refresh() {
  if (!me) return;
  try {
    games = await Api.listGames();
  } catch (e) {
    listsBox.innerHTML = `<div class="error-state">Couldn't load your games: ${escapeHtml(e.message)}</div>`;
    return;
  }
  const doneNow = games.mine.filter((g) => g.status === 'done');
  if (finishedIds) doneNow.filter((g) => !finishedIds.has(g.id)).forEach(toastFinished);
  finishedIds = new Set(doneNow.map((g) => g.id));
  renderLists();
  showDuelsDot();
}

function toastFinished(g) {
  const t = document.createElement('div');
  t.className = 'duel-toast';
  t.setAttribute('role', 'status');
  const word = { win: 'You won', loss: 'You lost', draw: "It's a draw" }[g.outcome] || 'Game over';
  t.innerHTML = `<span><b>${word}</b> · ${g.format} ${gameTitle(g)} · ${scoreLine(g)}</span><button type="button" class="pill-button btn-sm">Results</button>`;
  t.querySelector('button').addEventListener('click', () => {
    expanded.add(g.id);
    renderLists();
    document.querySelector(`.duel-row[data-id="${g.id}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    t.remove();
  });
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 12000);
}

async function renderLeaderboard() {
  try {
    const { rows } = await Api.leaderboard();
    if (!rows.length) { boardBox.innerHTML = '<div class="duel-note">No finished games yet. Be the first on the board.</div>'; return; }
    boardBox.innerHTML = `<ol class="lb">${rows.map((r, i) => `
      <li class="lb-row${me && r.username === me.username ? ' me' : ''}">
        <span class="lb-rank">${i + 1}</span>
        ${avatarHtml(r, 'lb-avatar')}
        <span class="lb-name">${escapeHtml(r.username)}</span>
        <span class="lb-rec" title="${r.wins} wins, ${r.draws} draws, ${r.losses} losses">${r.wins}–${r.draws}–${r.losses}</span>
      </li>`).join('')}</ol>
      <div class="duel-hint">Wins–draws–losses.</div>`;
  } catch (e) {
    boardBox.innerHTML = `<div class="form-error">${escapeHtml(e.message)}</div>`;
  }
}

// --- live updates -------------------------------------------------------------------------
// Like the Board: while the page is visible, ask every 8s whether anything
// changed (answered from the server's memory), and only then reload the
// games and leaderboard. Paused while a round is being played.

let liveVersion = null;
let liveTimer = null;

async function liveCheck() {
  clearTimeout(liveTimer);
  if (document.hidden) return;
  if (playBox.hidden && me) {
    try {
      const { version } = await Api.gamesVersion();
      if (liveVersion !== null && version !== liveVersion) await Promise.all([refresh(), renderLeaderboard()]);
      liveVersion = version;
    } catch { /* offline or deploying: try again next time */ }
  }
  liveTimer = setTimeout(liveCheck, 8000);
}

document.addEventListener('visibilitychange', () => { if (!document.hidden) liveCheck(); });

// --- playing ---------------------------------------------------------------------------
// One overlay per game: each round's two characters and a countdown. The
// server starts a round's clock when it hands the round out (a pick's
// response already carries the next round), and the time left comes from
// there - reopening mid-round continues it.

let timer = null;
let playing = null; // the game being played

function closePlay() {
  clearInterval(timer);
  playBox.hidden = true;
  playBox.innerHTML = '';
  document.body.classList.remove('duel-playing');
  playing = null;
  refresh();
}

function playFrame(inner) {
  playBox.hidden = false;
  document.body.classList.add('duel-playing');
  playBox.innerHTML = `<div class="duel-play-inner">${inner}</div>`;
}

async function play(gameId) {
  playing = [...games.mine, ...games.open].find((g) => g.id === gameId) || null;
  playFrame('<div class="duel-note">Loading the round…</div>');
  try {
    const { round } = await Api.nextRound(gameId);
    if (round) showRound(round);
    else await showFinished(gameId);
  } catch (e) {
    playFrame(`<div class="form-error">${escapeHtml(e.message)}</div><button type="button" class="pill-button" data-act="close">Close</button>`);
    playBox.querySelector('[data-act="close"]').addEventListener('click', closePlay);
  }
}

function showRound(r) {
  const title = playing ? `${playing.format} ${gameTitle(playing)}` : '';
  playFrame(`
    <div class="duel-play-head">
      <div>
        <div class="duel-play-round" id="play-title">Round ${r.round_no} of ${r.total}</div>
        <div class="duel-play-sub">${title} · who does the site say wins?</div>
      </div>
      <button type="button" class="duel-play-close" aria-label="Close (the clock keeps running)" title="Close (the clock keeps running)">✕</button>
    </div>
    <div class="duel-clock"><div class="duel-clock-bar"></div><span class="duel-clock-num"></span></div>
    <div class="duel-choices">
      ${[r.a, r.b].map((s) => `
        <button type="button" class="duel-choice" data-pick="${s.id}">
          ${sideTileHtml(s, 320)}
          <span class="duel-choice-name">${escapeHtml(bareName(s.name))}</span>
          <span class="duel-choice-series">${escapeHtml(s.series)}${s.form ? ` · ${escapeHtml(s.form)}` : ''}</span>
        </button>`).join('<span class="duel-choice-vs">vs</span>')}
    </div>
    <div class="duel-play-status" aria-live="polite"></div>`);
  const bar = playBox.querySelector('.duel-clock-bar');
  const num = playBox.querySelector('.duel-clock-num');
  const status = playBox.querySelector('.duel-play-status');
  const endAt = performance.now() + r.seconds_left * 1000;
  let locked = false;
  const lock = () => {
    locked = true;
    clearInterval(timer);
    playBox.querySelectorAll('.duel-choice').forEach((b) => { b.disabled = true; });
  };

  const tick = () => {
    const left = Math.max(0, (endAt - performance.now()) / 1000);
    bar.style.transform = `scaleX(${left / 20})`;
    bar.classList.toggle('urgent', left <= 5);
    num.textContent = Math.ceil(left);
    if (left <= 0 && !locked) {
      lock();
      status.textContent = "Time's up: this round counts as wrong.";
      setTimeout(() => play(r.game_id), 1000);
    }
  };
  clearInterval(timer);
  timer = setInterval(tick, 100);
  tick();

  playBox.querySelector('.duel-play-close').addEventListener('click', () => {
    if (locked || confirm("Close? This round's clock keeps running.")) closePlay();
  });
  playBox.querySelectorAll('.duel-choice').forEach((btn) => btn.addEventListener('click', async () => {
    if (locked) return;
    lock();
    btn.classList.add('chosen');
    status.textContent = 'Locked in';
    try {
      const res = await Api.pickRound(r.game_id, r.round_no, Number(btn.dataset.pick));
      if (!res.in_time) status.textContent = 'Too late: this round counts as wrong.';
      // The next round is already in the response - its clock started
      // with it, so show it straight away.
      setTimeout(() => (res.next ? showRound(res.next) : showFinished(r.game_id)), res.in_time ? 250 : 900);
    } catch (e) {
      status.textContent = e.message;
      setTimeout(() => play(r.game_id), 900);
    }
  }));
}

async function showFinished(gameId) {
  clearInterval(timer);
  let g = null;
  try { g = await Api.getGame(gameId); } catch { /* fall through with what we have */ }
  g = g || playing;
  const others = g ? g.players.filter((p) => !p.me && p.played < g.total).map((p) => p.username) : [];
  let body;
  if (g && g.status === 'done') {
    body = `<div class="duel-final ${g.outcome}">${{ win: 'You won', loss: 'You lost', draw: "It's a draw" }[g.outcome] || 'Game over'}</div>
      <div class="duel-final-score">${scoreLine(g)}</div>${resultsHtml(g)}`;
  } else {
    const wait = g && g.status === 'open'
      ? 'Results come once the game fills and everyone has played.'
      : `Results come once ${others.length ? others.map(escapeHtml).join(', ') : 'everyone'} ${others.length === 1 ? 'has' : 'have'} played.`;
    body = `<div class="duel-final">All ${g ? g.total : 5} locked in</div><div class="duel-note">${wait}</div>`;
  }
  playFrame(`
    <div class="duel-play-head">
      <div class="duel-play-round" id="play-title">${g ? `${g.format} ${gameTitle(g)}` : 'Game'}</div>
      <button type="button" class="duel-play-close" aria-label="Close">✕</button>
    </div>
    ${body}
    <button type="button" class="btn-gold duel-done-btn">Done</button>`);
  playBox.querySelectorAll('.duel-play-close, .duel-done-btn').forEach((b) => b.addEventListener('click', closePlay));
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !playBox.hidden && !playBox.querySelector('.duel-choice:not(:disabled)')) closePlay();
});

// --- start -----------------------------------------------------------------------------

(async () => {
  me = await currentUser();
  renderNew();
  renderLeaderboard();
  await refresh();
  liveCheck();
  // A shared link: duels.html?game=ID opens that game - to join it, or
  // straight to its rounds or results.
  const id = Number(new URLSearchParams(location.search).get('game'));
  if (!id || !me) return;
  const g = [...games.mine, ...games.open].find((x) => x.id === id) || await Api.getGame(id).catch(() => null);
  if (!g) return;
  if (g.can_join && confirm(`Join ${g.creator}'s ${g.format} and play now?`)) {
    try { await Api.joinGame(id, null); await refresh(); play(id); } catch (e) { alert(e.message); }
  } else if (g.can_play && g.my_played < g.total) {
    play(id);
  } else if (g.status === 'done') {
    expanded.add(id);
    renderLists();
    document.querySelector(`.duel-row[data-id="${id}"]`)?.scrollIntoView({ block: 'center' });
  }
})();
