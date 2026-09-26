// Prediction duels: make a challenge, see your games and the leaderboard,
// and play rounds against the clock. Rules live on the server
// (backend/duels.py) - the clock there is the one that counts; the one
// drawn here only shows it.

renderTopbar([]);

const newBox = document.getElementById('duel-new');
const listsBox = document.getElementById('duel-lists');
const boardBox = document.getElementById('leaderboard');
const playBox = document.getElementById('duel-play');
let me = null;
let games = { mine: [], open: [] };
const expanded = new Set(); // finished games whose rounds are shown

// --- small pieces ----------------------------------------------------------------

function playerHtml(p, cls = 'duel-avatar') {
  return p ? userAvatarHtml(p.username, p.avatar_url, cls, { admin: p.is_admin }) : '';
}

function other(g) {
  return g.me_is_creator ? g.opponent : g.creator;
}

function sideTileHtml(side, px) {
  return `<span class="duel-tile${side.image_url ? ' has-pic' : ''}" style="background:${accentFor(side.id)}">${
    characterTileInner(side.name, side.image_url, px)}</span>`;
}

function sideLabel(side) {
  return `${escapeHtml(bareName(side.name))}${side.form ? ` <span class="duel-form">${escapeHtml(side.form)}</span>` : ''}`;
}

function gameLink(id) {
  return `${location.origin}${location.pathname.replace(/[^/]*$/, '')}duels.html?game=${id}`;
}

// --- new challenge -----------------------------------------------------------------

function renderNew() {
  if (!me) {
    const next = encodeURIComponent('duels.html' + location.search);
    newBox.innerHTML = `
      <h2 class="duel-card-title" id="new-title">Start a duel</h2>
      <p class="duel-note"><a href="login.html?next=${next}">Log in</a> or <a href="login.html?mode=register&next=${next}">create an account</a> to challenge someone.</p>`;
    return;
  }
  newBox.innerHTML = `
    <h2 class="duel-card-title" id="new-title">New challenge</h2>
    <div class="duel-field">
      <span class="lbl">Opponent</span>
      <div class="seg" role="radiogroup" aria-label="Opponent">
        <button type="button" class="seg-btn active" data-opp="anyone" role="radio" aria-checked="true">Anyone</button>
        <button type="button" class="seg-btn" data-opp="user" role="radio" aria-checked="false">A specific user</button>
      </div>
      <input type="text" class="duel-input" id="duel-opponent" placeholder="Their username" maxlength="20" autocomplete="off" spellcheck="false" hidden>
    </div>
    <div class="duel-field">
      <span class="lbl">Matchups</span>
      <div class="seg" role="radiogroup" aria-label="Matchups">
        <button type="button" class="seg-btn active" data-mu="random" role="radio" aria-checked="true">Random</button>
        <button type="button" class="seg-btn" data-mu="pick" role="radio" aria-checked="false">Pick my own</button>
      </div>
      <div class="duel-pickers" hidden></div>
      <button type="button" class="duel-add" hidden>+ Add a matchup</button>
      <p class="duel-hint" id="duel-mu-hint">Five matchups drawn at random from characters of similar tiers.</p>
    </div>
    <div class="duel-actions">
      <span class="form-error" id="duel-error"></span>
      <button type="button" class="btn-gold" id="duel-create">Create challenge</button>
    </div>`;

  const oppInput = newBox.querySelector('#duel-opponent');
  const pickers = newBox.querySelector('.duel-pickers');
  const addBtn = newBox.querySelector('.duel-add');
  const hint = newBox.querySelector('#duel-mu-hint');
  const err = newBox.querySelector('#duel-error');
  const picked = new Map(); // picker element -> matchup or null
  let mode = 'random';

  const setSeg = (attr, value) => newBox.querySelectorAll(`[data-${attr}]`).forEach((b) => {
    const on = b.dataset[attr] === value;
    b.classList.toggle('active', on);
    b.setAttribute('aria-checked', String(on));
  });
  newBox.querySelectorAll('[data-opp]').forEach((b) => b.addEventListener('click', () => {
    setSeg('opp', b.dataset.opp);
    oppInput.hidden = b.dataset.opp !== 'user';
    if (!oppInput.hidden) oppInput.focus();
  }));

  const updateHint = () => {
    const n = picked.size;
    addBtn.hidden = mode !== 'pick' || n >= 5;
    hint.textContent = mode === 'random'
      ? 'Five matchups drawn at random from characters of similar tiers.'
      : `${n} picked${n < 5 ? `, ${5 - n} drawn at random` : ''}. Only clear wins count: "too close to call" matchups can't be used. You'll know the ones you pick; your opponent sees each only when its 20 seconds start.`;
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

  const createBtn = newBox.querySelector('#duel-create');
  createBtn.addEventListener('click', async () => {
    err.textContent = '';
    const opponent = oppInput.hidden ? null : oppInput.value.trim();
    if (!oppInput.hidden && !opponent) { err.textContent = 'Type their username, or pick "Anyone".'; return; }
    const matchups = mode === 'pick' ? [...picked.values()] : [];
    if (matchups.some((m) => !m)) { err.textContent = 'Finish each matchup, or remove it.'; return; }
    createBtn.disabled = true;
    createBtn.textContent = 'Creating…';
    try {
      const g = await Api.createGame(opponent, matchups);
      renderNew(); // a fresh form
      await refresh();
      showCreated(g);
    } catch (e) {
      err.textContent = e.message;
    } finally {
      createBtn.disabled = false;
      createBtn.textContent = 'Create challenge';
    }
  });
}

// Right after creating: play now, and the link to send.
function showCreated(g) {
  const box = document.createElement('div');
  box.className = 'duel-created';
  const who = g.open_to_anyone ? 'Anyone can take it from the open challenges.' : `${escapeHtml(g.opponent.username)} will see it on their Duels page.`;
  box.innerHTML = `
    <div class="duel-created-title">Challenge created</div>
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

function statusText(g) {
  const them = other(g);
  const theirName = them ? escapeHtml(them.username) : 'your opponent';
  switch (g.status) {
    case 'done': {
      const word = { win: 'Won', loss: 'Lost', draw: 'Draw' }[g.outcome] || 'Finished';
      return `<span class="duel-outcome ${g.outcome || ''}">${word} ${g.my_score}–${g.their_score}</span>`;
    }
    case 'expired': return 'Expired: nobody took it';
    case 'declined': return g.me_is_creator ? `${theirName} declined` : 'You declined';
    case 'cancelled': return 'Cancelled';
    default: break;
  }
  if (g.can_accept) return g.open_to_anyone ? `Open challenge from ${escapeHtml(g.creator.username)}` : `${escapeHtml(g.creator.username)} challenged you`;
  if (g.can_play && g.my_played < g.total) {
    return `Your turn · ${g.my_played}/${g.total} played`;
  }
  if (g.status === 'open') return g.open_to_anyone ? 'Waiting for someone to accept' : `Waiting for ${theirName} to accept`;
  return `Waiting for ${theirName} · ${g.their_played}/${g.total} played`;
}

function duelRow(g) {
  const el = document.createElement('div');
  el.className = 'duel-row';
  el.dataset.id = g.id;
  const them = other(g);
  const title = g.status === 'open' && g.open_to_anyone && g.me_is_creator
    ? 'Open challenge'
    : them ? `vs ${escapeHtml(them.username)}` : 'Open challenge';
  const buttons = [];
  if (g.can_accept) buttons.push('<button type="button" class="btn-gold btn-sm" data-act="accept">Accept &amp; play</button>');
  if (g.can_decline) buttons.push('<button type="button" class="pill-button btn-sm" data-act="decline">Decline</button>');
  if (g.can_play && g.my_played < g.total) buttons.push(`<button type="button" class="btn-gold btn-sm" data-act="play">${g.my_played ? 'Continue' : 'Play'}</button>`);
  if (g.can_cancel) buttons.push('<button type="button" class="pill-button btn-sm" data-act="cancel">Cancel</button>');
  if (g.status === 'done') buttons.push(`<button type="button" class="pill-button btn-sm" data-act="results" aria-expanded="${expanded.has(g.id)}">${expanded.has(g.id) ? 'Hide' : 'Rounds'}</button>`);
  el.innerHTML = `
    <div class="duel-row-main">
      ${them ? playerHtml(them) : '<span class="duel-avatar duel-avatar-open" aria-hidden="true">?</span>'}
      <div class="duel-row-text">
        <div class="duel-row-title">${title}${them && them.is_admin ? adminBadgeHtml() : ''}</div>
        <div class="duel-row-status">${statusText(g)}</div>
      </div>
      <div class="duel-row-buttons">${buttons.join('')}</div>
    </div>
    ${g.status === 'done' && expanded.has(g.id) ? resultsHtml(g) : ''}`;
  el.addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act) onRowAction(g, act, e.target.closest('button'));
  });
  return el;
}

function resultsHtml(g) {
  const them = other(g);
  const mark = (ok, picked) => picked == null ? '<span class="duel-mark miss" title="No answer">—</span>'
    : `<span class="duel-mark ${ok ? 'ok' : 'bad'}" title="${ok ? 'Right' : 'Wrong'}">${ok ? '✓' : '✗'}</span>`;
  const nameOf = (r, id) => id == null ? 'no answer' : id === r.a.id ? bareName(r.a.name) : bareName(r.b.name);
  return `
    <ol class="duel-rounds">
      ${g.rounds.map((r) => `
        <li class="duel-round">
          <div class="duel-round-head">
            <a href="${escapeHtml(r.compare_url)}" class="duel-round-vs">${sideLabel(r.a)} <span class="duel-vs">vs</span> ${sideLabel(r.b)}</a>
            ${r.picked ? '<span class="duel-picked" title="Chosen by the challenger">picked</span>' : ''}
          </div>
          <div class="duel-round-verdict">${escapeHtml(r.verdict)}</div>
          <div class="duel-round-picks">
            <span>${mark(r.my_correct, r.my_pick)} You: ${escapeHtml(nameOf(r, r.my_pick))}</span>
            <span>${mark(r.their_correct, r.their_pick)} ${escapeHtml(them ? them.username : 'Them')}: ${escapeHtml(nameOf(r, r.their_pick))}</span>
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
    if (btn) btn.disabled = true;
    if (act === 'accept') { await Api.acceptGame(g.id); await refresh(); return play(g.id); }
    if (act === 'decline') { if (!confirm('Decline this challenge?')) { btn.disabled = false; return; } await Api.declineGame(g.id); }
    if (act === 'cancel') { if (!confirm('Cancel this challenge?')) { btn.disabled = false; return; } await Api.cancelGame(g.id); }
    await refresh();
  } catch (e) {
    alert(e.message);
    if (btn) btn.disabled = false;
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
  const forYou = games.mine.filter((g) => live(g) && g.can_accept);
  const yourTurn = games.mine.filter((g) => live(g) && !g.can_accept && g.can_play && g.my_played < g.total);
  const waiting = games.mine.filter((g) => live(g) && !g.can_accept && !(g.can_play && g.my_played < g.total));
  const finished = games.mine.filter((g) => !live(g)).slice(0, 15);
  [
    section('Challenges for you', forYou),
    section('Your turn', yourTurn, forYou.length ? null : 'Nothing to play right now. Start a challenge, or take an open one.'),
    section('Waiting', waiting),
    section('Open challenges', games.open, 'No open challenges from others right now.'),
    section('Finished', finished),
  ].filter(Boolean).forEach((s) => listsBox.appendChild(s));
}

async function refresh() {
  if (!me) return;
  try {
    games = await Api.listGames();
  } catch (e) {
    listsBox.innerHTML = `<div class="error-state">Couldn't load your duels: ${escapeHtml(e.message)}</div>`;
    return;
  }
  renderLists();
  showDuelsDot();
}

async function renderLeaderboard() {
  try {
    const { rows } = await Api.leaderboard();
    if (!rows.length) { boardBox.innerHTML = '<div class="duel-note">No finished duels yet. Be the first on the board.</div>'; return; }
    boardBox.innerHTML = `<ol class="lb">${rows.map((r, i) => `
      <li class="lb-row${me && r.username === me.username ? ' me' : ''}">
        <span class="lb-rank">${i + 1}</span>
        ${playerHtml(r, 'lb-avatar')}
        <span class="lb-name">${escapeHtml(r.username)}</span>
        <span class="lb-rec" title="${r.wins} wins, ${r.draws} draws, ${r.losses} losses">${r.wins}–${r.draws}–${r.losses}</span>
      </li>`).join('')}</ol>
      <div class="duel-hint">Wins–draws–losses.</div>`;
  } catch (e) {
    boardBox.innerHTML = `<div class="form-error">${escapeHtml(e.message)}</div>`;
  }
}

// --- playing ---------------------------------------------------------------------------
// One overlay per game: each round's two characters and a countdown. The
// server starts a round's clock when it hands the round out, and the
// remaining time comes from there - reopening mid-round continues it.

let timer = null;

function closePlay() {
  clearInterval(timer);
  playBox.hidden = true;
  playBox.innerHTML = '';
  document.body.classList.remove('duel-playing');
  refresh();
}

async function play(gameId) {
  playBox.hidden = false;
  document.body.classList.add('duel-playing');
  playBox.innerHTML = '<div class="duel-play-inner"><div class="duel-note">Loading the round…</div></div>';
  let res;
  try {
    res = await Api.nextRound(gameId);
  } catch (e) {
    playBox.innerHTML = `<div class="duel-play-inner"><div class="form-error">${escapeHtml(e.message)}</div>
      <button type="button" class="pill-button" data-act="close">Close</button></div>`;
    playBox.querySelector('[data-act="close"]').addEventListener('click', closePlay);
    return;
  }
  if (!res.round) return showFinished(res.game);
  showRound(res.round, res.game);
}

function showRound(r, g) {
  const them = other(g);
  playBox.innerHTML = `
    <div class="duel-play-inner">
      <div class="duel-play-head">
        <div>
          <div class="duel-play-round" id="play-title">Round ${r.round_no} of ${r.total}</div>
          <div class="duel-play-sub">${them ? `vs ${escapeHtml(them.username)}` : 'Open challenge'} · who does the site say wins?</div>
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
      <div class="duel-play-status" aria-live="polite"></div>
    </div>`;
  const bar = playBox.querySelector('.duel-clock-bar');
  const num = playBox.querySelector('.duel-clock-num');
  const status = playBox.querySelector('.duel-play-status');
  const endAt = performance.now() + r.seconds_left * 1000;
  let locked = false;

  const tick = () => {
    const left = Math.max(0, (endAt - performance.now()) / 1000);
    bar.style.transform = `scaleX(${left / 20})`;
    bar.classList.toggle('urgent', left <= 5);
    num.textContent = Math.ceil(left);
    if (left <= 0 && !locked) {
      locked = true;
      clearInterval(timer);
      playBox.querySelectorAll('.duel-choice').forEach((b) => { b.disabled = true; });
      status.textContent = "Time's up: this round counts as wrong.";
      setTimeout(() => play(g.id), 1200);
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
    locked = true;
    clearInterval(timer);
    playBox.querySelectorAll('.duel-choice').forEach((b) => { b.disabled = true; });
    btn.classList.add('chosen');
    status.textContent = 'Locked in';
    try {
      const { in_time: inTime } = await Api.pickRound(g.id, r.round_no, Number(btn.dataset.pick));
      if (!inTime) status.textContent = 'Too late: this round counts as wrong.';
    } catch (e) {
      status.textContent = e.message;
    }
    setTimeout(() => play(g.id), 700);
  }));
}

function showFinished(g) {
  clearInterval(timer);
  const them = other(g);
  const body = g.status === 'done'
    ? `<div class="duel-final ${g.outcome}">${{ win: 'You won', loss: 'You lost', draw: "It's a draw" }[g.outcome] || 'Finished'}</div>
       <div class="duel-final-score">${g.my_score} – ${g.their_score}</div>
       ${resultsHtml(g)}`
    : `<div class="duel-final">All ${g.total} locked in</div>
       <div class="duel-note">${g.status === 'open'
          ? 'Results come once someone accepts and plays their rounds.'
          : `Results come once ${them ? escapeHtml(them.username) : 'your opponent'} has played (${g.their_played}/${g.total} so far).`}</div>`;
  playBox.innerHTML = `
    <div class="duel-play-inner">
      <div class="duel-play-head">
        <div class="duel-play-round" id="play-title">${them ? `vs ${escapeHtml(them.username)}` : 'Open challenge'}</div>
        <button type="button" class="duel-play-close" aria-label="Close">✕</button>
      </div>
      ${body}
      <button type="button" class="btn-gold duel-done-btn">Done</button>
    </div>`;
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
  // A shared link: duels.html?game=ID opens that game - to accept it, or
  // straight to its rounds or results.
  const id = Number(new URLSearchParams(location.search).get('game'));
  if (!id || !me) return;
  const g = [...games.mine, ...games.open].find((x) => x.id === id) || await Api.getGame(id).catch(() => null);
  if (!g) return;
  if (g.can_accept && confirm(`Accept ${g.creator.username}'s challenge and play now?`)) {
    try { await Api.acceptGame(id); await refresh(); play(id); } catch (e) { alert(e.message); }
  } else if (g.can_play && g.my_played < g.total) {
    play(id);
  } else if (g.status === 'done') {
    expanded.add(id);
    renderLists();
    document.querySelector(`.duel-row[data-id="${id}"]`)?.scrollIntoView({ block: 'center' });
  }
})();
