// Prediction duels, after the Duels design screens: the lobby (new game +
// your games), a game's own page (teams, or seats around a table for a
// free-for-all; results once it's over) and the round screen. Rules live
// on the server (backend/duels.py) - its clock is the one that counts; the
// one drawn here only shows it.

renderTopbar([]);

const lobbyView = document.getElementById('lobby-view');
const gameView = document.getElementById('game-view');
const newBox = document.getElementById('duel-new');
const listsBox = document.getElementById('duel-lists');
const boardBox = document.getElementById('leaderboard');
const playBox = document.getElementById('duel-play');
const FORMATS = ['1v1', '1v1v1', '1v1v1v1', '2v2', '2v2v2', '3v3'];
const CHECK = (size = 16) => `<svg width="${size}" height="${size}" viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.4"/><path d="m5.3 8.2 1.8 1.8 3.6-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const MARKS = {
  ok: '<svg width="7" height="7" viewBox="0 0 8 8" aria-hidden="true"><path d="M1 4l2 2 4-4" stroke="#14120F" stroke-width="1.5" fill="none"/></svg>',
  bad: '<svg width="7" height="7" viewBox="0 0 8 8" aria-hidden="true"><path d="M2 2l4 4M6 2 2 6" stroke="#14120F" stroke-width="1.5"/></svg>',
  miss: '',
};
let me = null;
let games = { mine: [], open: [] };
let series = []; // every series name, for "leave out" chips
let openGameId = null; // the game page being shown, if any
// ?invite=name, from a profile's Challenge button: prefills the new game once.
let challengeName = (new URLSearchParams(location.search).get('invite') || '').trim().slice(0, 20);

// --- small pieces ----------------------------------------------------------------

const avatar = (p, cls) => userAvatarHtml(p.username, p.avatar_url, cls, { admin: p.is_admin });
const isTeamGame = (g) => g.team_size > 1;
const isFreeForAll = (g) => g.team_size === 1 && g.teams > 2;
const teamLabel = (g, t) => (isTeamGame(g) ? `Team ${t}` : `Player ${t}`);
const names = (list) => list.map((p) => `<b>${escapeHtml(p.username)}</b>`).join(', ');
const tagText = (g) => `${g.mode === 'draft' ? 'Draft · ' : g.mode === 'gauntlet' ? 'Gauntlet · ' : ''}${g.format}`;
const MODE_NAME = { predict: 'Prediction', draft: 'Draft', gauntlet: 'Gauntlet' };
const findGame = (id) => [...games.mine, ...games.open].find((g) => g.id === id) || null;

// "vs duel_bob" / "with alice · vs bob, carl" from where you sit;
// "alice's 2v2" for someone else's open game.
function gameTitle(g) {
  const mine = g.players.find((p) => p.me);
  if (!mine) return `<b>${escapeHtml(g.creator)}</b>'s ${g.format}`;
  const mates = g.players.filter((p) => !p.me && p.team === mine.team);
  const rivals = g.players.filter((p) => p.team !== mine.team);
  const parts = [];
  if (mates.length) parts.push(`with ${names(mates)}`);
  if (rivals.length) parts.push(`vs ${names(rivals)}`);
  return parts.join(' · ') || 'Waiting for players';
}

// The line under a game's title: whose move it is, or how it ended.
function statusText(g) {
  switch (g.status) {
    case 'done': {
      const word = { win: 'Won', loss: 'Lost', draw: 'Draw' }[g.outcome] || 'Finished';
      return `<span class="duel-outcome ${g.outcome || ''}">${word} ${scoreLine(g)}</span>`;
    }
    case 'expired': return "Expired: it didn't fill in time";
    case 'declined': return 'Declined by an invited player';
    case 'cancelled': return 'Cancelled';
    default: break;
  }
  if (g.can_decline) {
    const team = g.join_teams.length === 1 ? ` · you'd join ${teamLabel(g, g.join_teams[0])}` : '';
    return `Invited by <b>${escapeHtml(g.creator)}</b>${team}`;
  }
  if (g.can_join) return `${gameTitle(g)} · ${g.seats_left} seat${g.seats_left === 1 ? '' : 's'} left`;
  const pending = g.status === 'open'
    ? (g.invited.length ? `waiting for ${g.invited.map((n) => `<b>${escapeHtml(n)}</b>`).join(', ')} to join`
      : `${g.seats_left} seat${g.seats_left === 1 ? '' : 's'} left${g.link_only ? ' · link only' : ''}`)
    : null;
  if (g.can_play && g.my_played < g.total) return `${gameTitle(g)} · Your turn · ${g.my_played}/${g.total} played${pending ? ` · ${pending}` : ''}`;
  if (pending) return `${gameTitle(g)} · ${pending}`;
  const left = g.players.filter((p) => p.played < g.total);
  return `Waiting for ${names(left)}`;
}

function scoreLine(g) {
  if (!g.team_scores.length) return '';
  if (g.teams === 2) return g.my_team === 2 ? `${g.team_scores[1]}–${g.team_scores[0]}` : g.team_scores.join('–');
  return g.team_scores.join(' · ');
}

// Square avatars grouped by team, empty seats dashed, a thin line between teams.
function seatsHtml(g) {
  const groups = Array.from({ length: g.teams }, (_, i) => {
    const team = g.players.filter((p) => p.team === i + 1);
    const cells = team.map((p) => avatar(p, 'duel-seat'));
    for (let k = team.length; k < g.team_size; k++) cells.push('<span class="duel-seat empty" aria-hidden="true"></span>');
    return cells.join('');
  });
  return `<span class="duel-seats">${groups.join('<span class="duel-seats-sep"></span>')}</span>`;
}

function sideTileHtml(side, px) {
  // Eager and high priority: the round's clock is already running. The
  // initial shows until the picture arrives, instead of an empty square.
  const pic = characterPictureUrl(side.image_url, px);
  return `<span class="duel-tile${pic ? ' has-pic' : ''}" style="background:${accentFor(side.id)}">
    <span class="tile-initial">${escapeHtml(initialFor(side.name))}</span>${pic
      ? `<img src="${escapeHtml(pic)}" alt="" fetchpriority="high" referrerpolicy="no-referrer"
          onload="this.parentNode.classList.add('loaded')" onerror="this.parentNode.classList.remove('has-pic');this.remove()">`
      : ''}</span>`;
}

function sideLabel(side) {
  return `${escapeHtml(shortName(side.name))}${side.form ? ` <span class="duel-form">${escapeHtml(side.form)}</span>` : ''}`;
}

function gameLink(id) {
  return `${location.origin}${location.pathname.replace(/[^/]*$/, '')}duels.html?game=${id}`;
}

// --- new game ------------------------------------------------------------------------

function renderNew() {
  if (!me) {
    const next = encodeURIComponent('duels.html' + location.search);
    newBox.className = 'duel-card duel-empty';
    newBox.innerHTML = `
      <div class="duel-empty-title" id="new-title">Log in to play Duels</div>
      <p class="duel-note">You'll need an account to create, join or be invited to games.</p>
      <a class="btn-gold" href="login.html?next=${next}">Log in</a>`;
    return;
  }
  newBox.className = 'duel-card';
  newBox.innerHTML = `
    <h2 class="duel-card-title" id="new-title">New game</h2>
    <div class="duel-field">
      <span class="duel-lbl" id="gm-lbl">Game</span>
      <div class="duel-toggle duel-toggle-3 duel-modes" role="radiogroup" aria-labelledby="gm-lbl">
        <button type="button" class="active" data-gm="predict" role="radio" aria-checked="true">Call the winner</button>
        <button type="button" data-gm="draft" role="radio" aria-checked="false">Draft</button>
        <button type="button" data-gm="gauntlet" role="radio" aria-checked="false">Gauntlet</button>
      </div>
    </div>
    <div class="duel-field">
      <span class="duel-lbl" id="fmt-lbl">Format</span>
      <div class="duel-formats" role="radiogroup" aria-labelledby="fmt-lbl">
        ${FORMATS.map((f, i) => `<button type="button" class="duel-fmt${i ? '' : ' active'}" data-fmt="${f}" role="radio" aria-checked="${!i}">${f}</button>`).join('')}
      </div>
    </div>
    <div class="duel-field">
      <span class="duel-lbl" id="who-lbl">Who can join</span>
      <div class="duel-toggle duel-toggle-3" role="radiogroup" aria-labelledby="who-lbl">
        <button type="button" class="active" data-who="open" role="radio" aria-checked="true">Open to anyone</button>
        <button type="button" data-who="link" role="radio" aria-checked="false">Friends with the link</button>
        <button type="button" data-who="invite" role="radio" aria-checked="false">Invite by name</button>
      </div>
      <p class="duel-hint" id="duel-who-hint"></p>
    </div>
    <div class="duel-field" id="invite-field" hidden>
      <span class="duel-lbl" id="invite-lbl"></span>
      <div class="duel-invites"></div>
    </div>
    <div class="duel-field" id="mu-field">
      <span class="duel-lbl" id="mu-lbl">Matchups</span>
      <div class="duel-toggle" role="radiogroup" aria-labelledby="mu-lbl">
        <button type="button" class="active" data-mu="random" role="radio" aria-checked="true">Random</button>
        <button type="button" data-mu="pick" role="radio" aria-checked="false">Pick my own</button>
      </div>
    </div>
    <div class="duel-field" id="gd-field" hidden>
      <span class="duel-lbl" id="gd-lbl">Opponents</span>
      <div class="duel-toggle duel-toggle-3" role="radiogroup" aria-labelledby="gd-lbl">
        <button type="button" class="active" data-gsrc="random" role="radio" aria-checked="true">From the whole roster</button>
        <button type="button" data-gsrc="series" role="radio" aria-checked="false">A series' best known</button>
        <button type="button" data-gsrc="custom" role="radio" aria-checked="false">My own picks</button>
      </div>
      <select class="duel-input gl-series" id="gd-series" aria-label="Series" hidden></select>
      <div id="gd-custom" hidden><div class="gl-picks" id="gd-picks"></div><div id="gd-custom-search"></div></div>
      <span class="duel-lbl gd-chars-lbl">Characters (optional)</span>
      <div class="gl-picks" id="gd-chars"></div>
      <div id="gd-chars-search"></div>
    </div>
    <div class="duel-field" id="pick-field" hidden>
      <span class="duel-lbl">Matchups (up to 5)</span>
      <div class="duel-pickers"></div>
      <button type="button" class="duel-add" hidden>+ Add a matchup</button>
    </div>
    <div class="duel-field" id="series-field">
      <span class="duel-lbl" id="series-lbl">Leave these series out</span>
      <div class="duel-out"></div>
      <div class="duel-series" id="duel-series" hidden>
        <input type="text" class="duel-input duel-series-filter" placeholder="Find a series…" maxlength="60" autocomplete="off"
          spellcheck="false" aria-label="Find a series to leave out">
        <div class="duel-series-list"></div>
      </div>
      <p class="duel-hint" id="duel-mu-hint"></p>
    </div>
    <div class="duel-actions">
      <button type="button" class="btn-gold" id="duel-create">Create game</button>
      <span class="form-error" id="duel-error"></span>
    </div>`;

  const inviteField = newBox.querySelector('#invite-field');
  const invitesBox = inviteField.querySelector('.duel-invites');
  const pickField = newBox.querySelector('#pick-field');
  const pickers = pickField.querySelector('.duel-pickers');
  const addBtn = pickField.querySelector('.duel-add');
  const seriesField = newBox.querySelector('#series-field');
  const outBox = seriesField.querySelector('.duel-out');
  const seriesBox = seriesField.querySelector('.duel-series');
  const seriesFilter = seriesBox.querySelector('.duel-series-filter');
  const seriesList = seriesBox.querySelector('.duel-series-list');
  const hint = newBox.querySelector('#duel-mu-hint');
  const err = newBox.querySelector('#duel-error');
  const picked = new Map(); // picker element -> matchup or null
  const excluded = new Set();
  let format = '1v1';
  let who = 'open'; // open | link | invite
  let inviting = false;
  let mode = 'random';
  let gameMode = 'predict';
  // Gauntlet: where the opponents come from, and characters picked to run it.
  const gd = { source: 'random', series: '', opponents: [], chars: [] };
  const gdChips = (list, key) => list.map((c, i) => `
    <span class="fav-chip gl-pick"><span class="fav-chip-tile${c.image_url ? ' has-pic' : ''}" style="background:${accentFor(c.id)}">${characterTileInner(c.name, c.image_url, 52)}</span>
      <span class="fav-chip-name">${escapeHtml(shortName(c.name))}</span>
      <button type="button" class="fav-chip-clear" data-${key}="${i}" aria-label="Remove">×</button></span>`).join('');
  const drawGauntlet = () => {
    newBox.querySelectorAll('[data-gsrc]').forEach((b) => {
      const on = b.dataset.gsrc === gd.source;
      b.classList.toggle('active', on);
      b.setAttribute('aria-checked', String(on));
    });
    newBox.querySelector('#gd-series').hidden = gd.source !== 'series';
    newBox.querySelector('#gd-custom').hidden = gd.source !== 'custom';
    newBox.querySelector('#gd-picks').innerHTML = gdChips(gd.opponents, 'opp');
    newBox.querySelector('#gd-custom-search').hidden = gd.opponents.length >= 10;
    newBox.querySelector('#gd-chars').innerHTML = gdChips(gd.chars, 'chr');
    newBox.querySelector('#gd-chars-search').hidden = gd.chars.length >= 5;
  };

  const setRadio = (attr, value) => newBox.querySelectorAll(`[data-${attr}]`).forEach((b) => {
    const on = b.dataset[attr] === value;
    b.classList.toggle('active', on);
    b.setAttribute('aria-checked', String(on));
  });
  const seats = () => format.split('v').reduce((n, s) => n + Number(s), 0) - 1;
  // One username box per seat to fill, keeping whatever was typed.
  const drawInvites = () => {
    inviteField.hidden = !inviting;
    newBox.querySelector('#duel-who-hint').textContent = {
      open: 'Listed under Open games and posted on our Discord, so anyone can join.',
      link: "Not listed or posted anywhere: only people you send the link to can join. You'll get the link once it's created.",
      invite: 'Only the players you name can join. It shows up on their Duels page.',
    }[who];
    newBox.querySelector('#invite-lbl').textContent = `Invite seats (${seats()} open)`;
    const typed = [...invitesBox.querySelectorAll('input')].map((i) => i.value);
    invitesBox.innerHTML = Array.from({ length: seats() }, (_, i) => `
      <input type="text" class="duel-input" placeholder="Invite by username…" maxlength="20" autocomplete="off"
        spellcheck="false" aria-label="Invited player ${i + 1}" value="${escapeHtml(typed[i] || '')}">`).join('');
  };
  // Left-out series as removable chips, then "+ Add series", which opens
  // a filterable list of the rest.
  const drawSeries = () => {
    outBox.innerHTML = [...excluded].map((s) => `
      <span class="duel-out-chip">${escapeHtml(s)}<button type="button" data-keep="${escapeHtml(s)}" aria-label="Put ${escapeHtml(s)} back">×</button></span>`).join('')
      + `<button type="button" class="duel-out-add" aria-expanded="${!seriesBox.hidden}" aria-controls="duel-series">${seriesBox.hidden ? '+ Add series' : 'Done'}</button>`;
    const q = seriesFilter.value.trim().toLowerCase();
    const rest = series.filter((s) => !excluded.has(s) && (!q || s.toLowerCase().includes(q)));
    seriesList.innerHTML = rest.map((s) => `<button type="button" class="duel-series-chip" data-series="${escapeHtml(s)}">${escapeHtml(s)}</button>`).join('')
      || '<span class="duel-hint">No series match.</span>';
  };
  const update = () => {
    const n = picked.size;
    const draft = gameMode === 'draft';
    const gauntletMode = gameMode === 'gauntlet';
    newBox.querySelector('#mu-field').hidden = draft || gauntletMode;
    newBox.querySelector('#gd-field').hidden = !gauntletMode;
    pickField.hidden = draft || gauntletMode || mode !== 'pick';
    addBtn.hidden = draft || gauntletMode || mode !== 'pick' || n >= 5;
    seriesField.hidden = !draft && !gauntletMode && mode === 'pick' && n >= 5; // nothing random left to draw
    if (gauntletMode) {
      hint.textContent = "Each round shows a character and its ladder of opponents, weakest first. Guess how many it beats before its first loss: 3 points for the exact number, 2 if you're one off, 1 if two off. 30 seconds a round. Characters you don't pick are drawn at random.";
      return;
    }
    if (draft) {
      hint.textContent = 'Each round everyone is dealt 4 characters of similar tiers and picks the one they think is strongest. Picks then go head to head: each win is a point, and dead-even picks go to whoever locked in faster.';
      return;
    }
    hint.textContent = mode === 'random'
      ? 'Five matchups between characters of similar tiers.'
      : `${n} picked${n < 5 ? `, ${5 - n} drawn at random` : ''}. Only clear wins count: "too close to call" matchups can't be used. You'll know the ones you pick; the others see each only when its 20 seconds start.`;
  };
  const addPicker = () => {
    const el = matchupPickerEl({
      verdict: false,
      onChange: (m) => picked.set(el, m),
      onClose: () => { picked.delete(el); el.remove(); update(); },
    });
    picked.set(el, null);
    pickers.appendChild(el);
    update();
    el.focusFirst();
  };

  newBox.querySelectorAll('[data-gm]').forEach((b) => b.addEventListener('click', () => {
    gameMode = b.dataset.gm;
    setRadio('gm', gameMode);
    update();
  }));
  newBox.querySelectorAll('[data-fmt]').forEach((b) => b.addEventListener('click', () => {
    format = b.dataset.fmt;
    setRadio('fmt', format);
    drawInvites();
  }));
  const setWho = (value) => {
    who = value;
    inviting = who === 'invite';
    setRadio('who', who);
    drawInvites();
  };
  newBox.querySelectorAll('[data-who]').forEach((b) => b.addEventListener('click', () => {
    setWho(b.dataset.who);
    if (inviting) invitesBox.querySelector('input')?.focus();
  }));
  newBox.querySelectorAll('[data-mu]').forEach((b) => b.addEventListener('click', () => {
    mode = b.dataset.mu;
    setRadio('mu', mode);
    if (mode === 'pick' && !picked.size) addPicker();
    update();
  }));
  addBtn.addEventListener('click', addPicker);
  const gdSeries = newBox.querySelector('#gd-series');
  gdSeries.innerHTML = '<option value="">Pick a series…</option>' + series.map((x) => `<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join('');
  gdSeries.addEventListener('change', () => { gd.series = gdSeries.value; });
  newBox.querySelectorAll('[data-gsrc]').forEach((b) => b.addEventListener('click', () => { gd.source = b.dataset.gsrc; drawGauntlet(); }));
  newBox.querySelector('#gd-field').addEventListener('click', (e) => {
    const x = e.target.closest('[data-opp], [data-chr]');
    if (!x) return;
    if (x.dataset.opp !== undefined) gd.opponents.splice(Number(x.dataset.opp), 1);
    else gd.chars.splice(Number(x.dataset.chr), 1);
    drawGauntlet();
  });
  newBox.querySelector('#gd-custom-search').appendChild(characterSearchEl({
    placeholder: 'Add an opponent…', label: 'Add an opponent', exclude: () => gd.opponents.map((c) => c.id),
    onChoose: (c) => { if (gd.opponents.length < 10) gd.opponents.push(c); drawGauntlet(); },
  }));
  newBox.querySelector('#gd-chars-search').appendChild(characterSearchEl({
    placeholder: 'Pick a character to run it (up to 5)…', label: 'Pick a character', exclude: () => gd.chars.map((c) => c.id),
    onChoose: (c) => { if (gd.chars.length < 5) gd.chars.push(c); drawGauntlet(); },
  }));
  drawGauntlet();
  outBox.addEventListener('click', (e) => {
    const keep = e.target.closest('[data-keep]');
    if (keep) excluded.delete(keep.dataset.keep);
    else if (e.target.closest('.duel-out-add')) {
      seriesBox.hidden = !seriesBox.hidden;
      seriesFilter.value = '';
    } else return;
    drawSeries();
    if (!seriesBox.hidden && !keep) seriesFilter.focus();
  });
  seriesFilter.addEventListener('input', drawSeries);
  seriesList.addEventListener('click', (e) => {
    const chip = e.target.closest('[data-series]');
    if (!chip) return;
    excluded.add(chip.dataset.series);
    drawSeries();
  });
  drawInvites();
  drawSeries();
  update();
  // From a profile's Challenge button: that player, invited to a 1v1.
  if (challengeName) {
    setWho('invite');
    invitesBox.querySelector('input').value = challengeName;
    challengeName = '';
    const url = new URL(location.href);
    url.searchParams.delete('invite');
    history.replaceState(history.state, '', url);
    newBox.scrollIntoView({ block: 'start' });
    newBox.querySelector('#duel-create').focus({ preventScroll: true });
  }

  const createBtn = newBox.querySelector('#duel-create');
  createBtn.addEventListener('click', async () => {
    err.textContent = '';
    const invite = inviting ? [...invitesBox.querySelectorAll('input')].map((i) => i.value.trim()) : [];
    if (invite.some((n) => !n)) { err.textContent = `Fill in all ${seats()} players, or let anyone join.`; return; }
    const matchups = gameMode === 'predict' && mode === 'pick' ? [...picked.values()] : [];
    if (matchups.some((m) => !m)) { err.textContent = 'Finish each matchup, or remove it.'; return; }
    if (gameMode === 'gauntlet' && gd.source === 'series' && !gd.series) { err.textContent = 'Pick the series to fight.'; return; }
    if (gameMode === 'gauntlet' && gd.source === 'custom' && gd.opponents.length < 2) { err.textContent = 'Add at least two opponents.'; return; }
    createBtn.disabled = true;
    createBtn.textContent = 'Creating…';
    try {
      const g = await Api.createGame(format, invite, matchups, [...excluded], gameMode, who === 'link', gameMode === 'gauntlet' ? {
        source: gd.source, series: gd.series, opponents: gd.opponents.map((c) => c.id), challengers: gd.chars.map((c) => c.id),
      } : {});
      renderNew(); // a fresh form
      showCreated(g);
      refresh(); // in the background: "Play now" needn't wait for the lists
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
  const who = g.private ? `${g.invited.map((n) => `<b>${escapeHtml(n)}</b>`).join(', ')} will see it on their Duels page.`
    : g.link_only ? 'Send the link to whoever you want to play: only they can find it.'
    : 'Anyone can join it from the open games.';
  // A link-only game fills only through its link: that comes first.
  const playHtml = `<button type="button" class="${g.link_only ? 'pill-button' : 'btn-gold'}" data-act="play">Play now</button>`;
  const copyHtml = `<button type="button" class="${g.link_only ? 'btn-gold' : 'pill-button'}" data-act="copy">Copy challenge link</button>`;
  box.innerHTML = `
    <div class="duel-created-title">${tagText(g)} created</div>
    <div class="duel-note">${who} Play your five rounds whenever you're ready.</div>
    <div class="duel-created-row">${g.link_only ? copyHtml + playHtml : playHtml + copyHtml}</div>`;
  box.querySelector('[data-act="play"]').addEventListener('click', () => {
    box.remove(); // done with it: the game is in Your games from here
    play(g.id, g);
  });
  const copy = box.querySelector('[data-act="copy"]');
  copy.addEventListener('click', () => copyLink(g.id, copy));
  newBox.prepend(box);
}

async function copyLink(id, btn) {
  const label = btn.textContent;
  try { await navigator.clipboard.writeText(gameLink(id)); btn.textContent = 'Link copied'; } catch { btn.textContent = 'Copy failed'; }
  setTimeout(() => { btn.textContent = label; }, 1800);
}

// --- your games ------------------------------------------------------------------------

// The buttons a game offers you, in the row and on its page.
function actionButtons(g, { page = false } = {}) {
  const b = [];
  if (g.can_join) {
    if (isTeamGame(g) && g.join_teams.length > 1) {
      g.join_teams.forEach((t, i) => b.push(`<button type="button" class="${i ? 'pill-button' : 'btn-gold'}" data-act="join" data-team="${t}">Join ${teamLabel(g, t)}</button>`));
    } else {
      b.push(`<button type="button" class="btn-gold" data-act="join">Join${g.join_teams.length === 1 && isTeamGame(g) ? ` ${teamLabel(g, g.join_teams[0])}` : ''}</button>`);
    }
  }
  if (g.can_play && g.my_played < g.total) b.push(`<button type="button" class="btn-gold" data-act="play">${g.my_played ? 'Continue' : 'Play'}</button>`);
  if (g.can_decline) b.push('<button type="button" class="pill-button" data-act="decline">Decline</button>');
  if (page && g.can_leave) b.push('<button type="button" class="pill-button" data-act="leave">Leave game</button>');
  if (g.can_cancel) b.push(`<button type="button" class="pill-button" data-act="cancel">${page ? 'Cancel game' : 'Cancel'}</button>`);
  if (page && (g.status === 'open' || g.status === 'active')) {
    b.push(`<button type="button" class="pill-button" data-act="copy">${g.status === 'open' ? 'Copy challenge link' : 'Copy link'}</button>`);
  }
  if (!page && g.status === 'done') b.push('<button type="button" class="pill-button" data-act="open">Results</button>');
  if (!page && !b.length) b.push('<button type="button" class="pill-button" data-act="open">View</button>');
  return b.join('');
}

function duelRow(g) {
  const el = document.createElement('div');
  el.className = 'duel-row' + (g.can_decline ? ' invite' : '');
  el.dataset.id = g.id;
  el.innerHTML = `
    <span class="duel-tag">${tagText(g)}</span>
    ${seatsHtml(g)}
    <div class="duel-row-text">${statusText(g)}</div>
    <div class="duel-row-buttons">${actionButtons(g)}</div>`;
  el.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-act]');
    if (btn) onAction(g, btn.dataset.act, btn);
    else openGame(g.id);
  });
  return el;
}

async function onAction(g, act, btn) {
  try {
    if (act === 'play') return play(g.id, g);
    if (act === 'open') return openGame(g.id);
    if (act === 'copy') return copyLink(g.id, btn);
    const ask = { decline: 'Decline this invite?', cancel: 'Cancel this game?', leave: 'Leave this game?' }[act];
    if (ask && !confirm(ask)) return;
    btn.disabled = true;
    if (act === 'join') {
      // Straight into round 1; the lists catch up in the background.
      const joined = await Api.joinGame(g.id, Number(btn.dataset.team) || null);
      play(g.id, joined);
      refresh();
      return;
    }
    if (act === 'decline') await Api.declineGame(g.id);
    if (act === 'cancel') await Api.cancelGame(g.id);
    if (act === 'leave') await Api.leaveGame(g.id);
    await refresh();
  } catch (e) {
    alert(e.message);
    refresh();
  }
}

function section(title, list) {
  if (!list.length) return null;
  const el = document.createElement('section');
  el.className = 'duel-section';
  el.innerHTML = `<h2 class="duel-section-title">${title} <span class="duel-count">${list.length}</span></h2>`;
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
  const sections = [
    section('Invites for you', forYou),
    section('Your turn', yourTurn),
    section('Waiting', waiting),
    section('Open games', games.open),
    section('Finished', finished),
  ].filter(Boolean);
  if (!sections.length) {
    listsBox.innerHTML = `
      <div class="duel-card duel-empty">
        <div class="duel-empty-title">No duels yet</div>
        <p class="duel-note">Start a game above. Open games from other players show up here too.</p>
      </div>`;
    return;
  }
  sections.forEach((s) => listsBox.appendChild(s));
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
  if (finishedIds) doneNow.filter((g) => !finishedIds.has(g.id) && g.id !== openGameId).forEach(toastFinished);
  finishedIds = new Set(doneNow.map((g) => g.id));
  renderLists();
  showDuelsDot(games.mine.filter((g) => (g.status === 'open' && g.can_decline)
    || (g.can_play && g.my_played < g.total)).length);
  if (openGameId) renderGame(await Api.getGame(openGameId).catch(() => findGame(openGameId)));
}

function toastFinished(g) {
  const t = document.createElement('div');
  t.className = 'duel-toast';
  t.setAttribute('role', 'status');
  const word = { win: 'You won', loss: 'You lost', draw: "It's a draw" }[g.outcome] || 'Game over';
  t.innerHTML = `<span class="duel-toast-icon ${g.outcome || 'draw'}">${CHECK(15)}</span>
    <span><b>${word}</b> · ${g.format} · ${scoreLine(g)}</span>
    <button type="button" class="pill-button btn-sm">Results</button>`;
  t.querySelector('button').addEventListener('click', () => { t.remove(); openGame(g.id); });
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
        ${avatar(r, 'lb-avatar')}
        <a class="lb-name" href="user.html?u=${encodeURIComponent(r.username)}">${escapeHtml(r.username)}${me && r.username === me.username ? ' (you)' : ''}</a>${titleHtml(r.title, 'utitle-sm')}
        <span class="lb-rec" title="${r.wins} wins, ${r.draws} draws, ${r.losses} losses">${r.wins}–${r.draws}–${r.losses}</span>
      </li>`).join('')}</ol>
      <div class="duel-hint">Wins–draws–losses.</div>`;
  } catch (e) {
    boardBox.innerHTML = `<div class="form-error">${escapeHtml(e.message)}</div>`;
  }
}

// --- a game's own page --------------------------------------------------------------------
// duels.html?game=ID: its seats by team (or around a table for a
// free-for-all), what's left to do, and once it's over the results.

async function openGame(id, { push = true } = {}) {
  openGameId = id;
  if (push) history.pushState({ game: id }, '', `duels.html?game=${id}`);
  lobbyView.hidden = true;
  gameView.hidden = false;
  gameView.innerHTML = '<div class="duel-loading"><span class="duel-spinner"></span>Loading…</div>';
  window.scrollTo({ top: 0 });
  try {
    renderGame(await Api.getGame(id));
  } catch (e) {
    gameView.innerHTML = `<button type="button" class="duel-back">← All games</button><div class="error-state">${escapeHtml(e.message)}</div>`;
    gameView.querySelector('.duel-back').addEventListener('click', () => closeGame());
  }
}

function closeGame({ push = true } = {}) {
  openGameId = null;
  if (push) history.pushState({}, '', 'duels.html');
  gameView.hidden = true;
  gameView.innerHTML = '';
  lobbyView.hidden = false;
}

window.addEventListener('popstate', () => {
  const id = Number(new URLSearchParams(location.search).get('game'));
  if (id) openGame(id, { push: false }); else closeGame({ push: false });
});

function slotHtml(g, p, cls = '') {
  const sub = g.status !== 'done' ? `${p.played}/${g.total} played`
    : g.mode === 'draft' || g.mode === 'gauntlet' ? `${p.score} point${p.score === 1 ? '' : 's'}` : `${p.score}/${g.total} right`;
  return `<div class="duel-slot ${cls}">
    ${avatar(p, `duel-slot-avatar${p.me ? ' me' : ''}`)}
    <div class="duel-slot-text"><div class="duel-slot-name">${escapeHtml(p.username)}${p.me ? ' (you)' : ''}${titleHtml(p.title, 'utitle-sm')}</div><div class="duel-slot-sub">${sub}</div></div>
  </div>`;
}

function emptySlotHtml(g, team, cls = '', invitee = null) {
  const joinable = g.can_join && g.join_teams.includes(team);
  return `<div class="duel-slot ${cls}">
    <span class="duel-slot-avatar empty" aria-hidden="true"></span>
    <div class="duel-slot-text">${invitee
      ? `<div class="duel-slot-name muted">${escapeHtml(invitee)}</div><div class="duel-slot-sub">Invited · not joined yet</div>`
      : '<div class="duel-slot-name muted">Open seat</div>'}</div>
    ${joinable ? `<button type="button" class="btn-gold" data-act="join" data-team="${team}">${isTeamGame(g) ? 'Join this team' : 'Join'}</button>` : ''}
  </div>`;
}

// Everyone's seat. Invited players who haven't joined fill empty seats
// first, in order, so a private game shows who it's waiting for.
function seatingHtml(g) {
  const invitees = [...g.invited];
  if (isFreeForAll(g)) {
    const slots = Array.from({ length: g.teams }, (_, i) => {
      const p = g.players.find((x) => x.team === i + 1);
      return p ? slotHtml(g, p, `seat-${i + 1}`) : emptySlotHtml(g, i + 1, `seat-${i + 1}`, invitees.shift());
    });
    return `<div class="duel-table">${slots.join('')}
      <div class="duel-table-center"><b>${g.format}</b><span>${g.seats_left ? `${g.seats_left} seat${g.seats_left === 1 ? '' : 's'} left` : 'Full'}</span></div></div>`;
  }
  const teams = Array.from({ length: g.teams }, (_, i) => {
    const t = i + 1;
    const members = g.players.filter((p) => p.team === t);
    const mine = g.my_team === t;
    const slots = members.map((p) => slotHtml(g, p));
    for (let k = members.length; k < g.team_size; k++) slots.push(emptySlotHtml(g, t, '', invitees.shift()));
    return `<div class="duel-team${mine ? ' mine' : ''}">
      <div class="duel-team-name">${teamLabel(g, t)}${mine ? ' · your team' : ''}</div>${slots.join('')}</div>`;
  });
  return `<div class="duel-teams">${teams.join('<span class="duel-teams-vs">vs</span>')}</div>`;
}

function gauntletSource(g) {
  const src = g.gauntlet || 'random';
  return src.startsWith('series:') ? `${src.slice(7)}'s best known` : src === 'custom' ? 'Opponents picked by the creator' : 'Opponents from the whole roster';
}

function infoHtml(g) {
  if (g.mode === 'gauntlet') {
    return `<div class="duel-info">
      <div class="duel-info-row"><span class="duel-lbl" style="margin:0">Gauntlet</span><span class="duel-info-pill">${escapeHtml(gauntletSource(g))} · 5 rounds</span>
        <span>Each ladder appears only when its round's 30 seconds start.</span></div>
      ${g.excluded.length ? `<div class="duel-info-row">Random picks leave out: ${g.excluded.map(escapeHtml).join(', ')}</div>` : ''}
    </div>`;
  }
  if (g.mode === 'draft') {
    return `<div class="duel-info">
      <div class="duel-info-row"><span class="duel-lbl" style="margin:0">Draft</span><span class="duel-info-pill">4 characters each · 5 rounds</span>
        <span>Each hand appears only when its round's 20 seconds start.</span></div>
      ${g.excluded.length ? `<div class="duel-info-row">Hands leave out: ${g.excluded.map(escapeHtml).join(', ')}</div>` : ''}
    </div>`;
  }
  const random = g.picked == null ? '' : g.picked === 0 ? 'R1–5 · Random' : g.picked === g.total ? 'All 5 picked by the creator' : `${g.picked} picked · ${g.total - g.picked} random`;
  return `<div class="duel-info">
    <div class="duel-info-row"><span class="duel-lbl" style="margin:0">Matchups</span>${random ? `<span class="duel-info-pill">${random}</span>` : ''}
      <span>Each one appears only when its round's 20 seconds start.</span></div>
    ${g.excluded.length ? `<div class="duel-info-row">Random rounds leave out: ${g.excluded.map(escapeHtml).join(', ')}</div>` : ''}
  </div>`;
}

function gameHeading(g) {
  if (g.status === 'open') return 'Waiting for players';
  if (g.status === 'active') return g.can_play && g.my_played < g.total ? 'Your turn' : 'In progress';
  return { expired: 'Expired', declined: 'Declined', cancelled: 'Cancelled', done: 'Finished' }[g.status] || '';
}

function renderGame(g) {
  if (!g || g.id !== openGameId) return;
  const back = '<button type="button" class="duel-back">← All games</button>';
  if (g.status === 'done') {
    gameView.innerHTML = `${back}${resultsHtml(g)}`;
    wireResultShare(gameView, g);
  } else {
    const mine = g.players.find((p) => p.me);
    const doneWaiting = mine && mine.played >= g.total && (g.status === 'open' || g.status === 'active');
    const stillPlaying = g.players.filter((p) => !p.me && p.played < g.total);
    gameView.innerHTML = `${back}
      ${challengeHtml(g)}
      <div class="duel-game-head">
        <span class="duel-tag">${tagText(g)}</span>
        <h1 class="duel-game-title">${gameHeading(g)}</h1>
        <div class="duel-game-actions">${actionButtons(g, { page: true })}</div>
      </div>
      ${seatingHtml(g)}
      ${doneWaiting ? `<div class="duel-waiting">
        <div class="duel-waiting-text"><div class="duel-waiting-title">You're all done</div>
          <div class="duel-note">Results show once everyone has played${g.status === 'open' ? ' and every seat is filled' : ''}.</div></div>
        ${stillPlaying.length ? `<div class="duel-waiting-people">${stillPlaying.map((p) => `
          <div class="duel-person">${avatar(p, 'duel-person-avatar')}<div class="duel-person-name">${escapeHtml(p.username)}</div>
          <div class="duel-person-name">still playing</div></div>`).join('')}</div>` : ''}
      </div>` : ''}
      ${infoHtml(g)}`;
  }
  gameView.querySelector('.duel-back').addEventListener('click', () => closeGame());
  gameView.querySelectorAll('[data-act]').forEach((btn) => btn.addEventListener('click', () => onAction(g, btn.dataset.act, btn)));
}

// Someone opened a friend's game link: say who's challenging them, and
// get them in - straight to round 1, or to log in and come back here.
function challengeHtml(g) {
  if (g.status !== 'open' || g.players.some((p) => p.me)) return '';
  const kind = `${MODE_NAME[g.mode] || 'Prediction'} duel · ${g.format}`;
  const how = g.mode === 'draft' ? 'Five rounds: pick the strongest from a hand of four.'
    : g.mode === 'gauntlet' ? 'Five characters, each against a ladder of ever-stronger opponents: guess how far each one climbs.'
      : "Five matchups, 20 seconds each: call who the site says wins.";
  let action;
  if (!me) {
    const next = encodeURIComponent(`duels.html?game=${g.id}`);
    action = `<a class="btn-gold" href="login.html?next=${next}">Log in to play</a>
      <a class="duel-challenge-alt" href="login.html?mode=register&next=${next}">or create an account</a>`;
  } else if (g.can_join) {
    action = '<button type="button" class="btn-gold" data-act="join">Join &amp; play</button>';
  } else {
    return '';  // invite only, and not you
  }
  return `<div class="duel-challenge">
    <div class="duel-challenge-kicker">⚔️ You're challenged</div>
    <div class="duel-challenge-title">${escapeHtml(g.creator)} wants to duel</div>
    <div class="duel-challenge-sub">${kind} · ${how}</div>
    <div class="duel-challenge-actions">${action}</div>
  </div>`;
}

function wireResultShare(container, g) {
  const btn = container.querySelector('.duel-result-share');
  if (!btn) return;
  attachShareMenu(btn, () => {
    const mine = g.players.find((p) => p.me);
    const scores = g.teams === 2 && g.my_team === 2 ? [...g.team_scores].reverse() : g.team_scores;
    const kind = `${MODE_NAME[g.mode] || 'Prediction'} duel (${g.format})`;
    const text = mine
      ? `${{ win: 'I won', loss: 'I lost', draw: 'I drew' }[g.outcome] || 'I played'} a ${kind} on Powerscale, ${scores.join('–')}. Think you can call who wins?`
      : `A ${kind} on Powerscale: ${g.players.map((p) => p.username).join(' vs ')}, ${g.team_scores.join('–')}.`;
    return { url: gameLink(g.id), title: `Powerscale ${kind}`, text };
  });
}

// Headline (you won / lost, the score, everyone's points by team), then
// each round: the matchup, what decided it, and every player's ✓ or ✗.
function resultsHtml(g) {
  const outcome = g.outcome || '';
  const title = { win: 'You won', loss: 'You lost', draw: "It's a draw" }[outcome] || 'Finished';
  // Two sides: yours on the left, like the score. More: in team order.
  const teamsSorted = g.teams === 2 && g.my_team === 2 ? [2, 1] : Array.from({ length: g.teams }, (_, i) => i + 1);
  const score = g.teams === 2 && isTeamGame(g)
    ? `${teamLabel(g, teamsSorted[0])} ${g.team_scores[teamsSorted[0] - 1]} — ${g.team_scores[teamsSorted[1] - 1]} ${teamLabel(g, teamsSorted[1])}`
    : g.teams === 2 ? scoreLine(g).replace('–', ' — ') // yours first
      : g.team_scores.map((s, i) => `${teamLabel(g, i + 1)}: ${s}`).join(' · ');
  const person = (p) => `<div class="duel-person" title="${escapeHtml(p.username)}">${avatar(p, `duel-person-avatar${p.me ? ' me' : ''}`)}
    <div class="duel-person-name">${escapeHtml(p.me ? 'you' : p.username)}</div><div class="duel-person-score">${p.score}</div></div>`;
  const people = teamsSorted.map((t) => `<div class="duel-result-team">${g.players.filter((p) => p.team === t).map(person).join('')}</div>`).join('');
  const byName = Object.fromEntries(g.players.map((p) => [p.username, p]));
  const pick = (r, p) => {
    const player = byName[p.username] || { username: p.username };
    const kind = p.pick_id == null ? 'miss' : p.correct ? 'ok' : 'bad';
    const said = p.pick_id == null ? 'no answer' : p.pick_id === r.a.id ? shortName(r.a.name) : shortName(r.b.name);
    return `<div class="duel-pick" title="${escapeHtml(`${p.username}: ${said}`)}">
      <div class="duel-pick-face">${avatar(player, 'duel-pick-avatar')}<span class="duel-pick-mark ${kind}">${MARKS[kind]}</span></div>
      <div class="duel-pick-name">${escapeHtml(player.me ? 'you' : p.username)}</div></div>`;
  };
  // A draft round: one row per player - their hand (gold ring: their
  // pick, star: the hand's highest tier) and the points it won.
  const rivalsOf = (p) => g.players.filter((x) => x.team !== p.team).length;
  const STAR = '<svg class="dr-star" width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1l1.8 3.6 4 .6-2.9 2.8.7 4-3.6-1.9-3.6 1.9.7-4L2.2 5.2l4-.6Z" fill="#D9A441"/></svg>';
  const draftRow = (p) => {
    const player = byName[p.username] || { username: p.username, team: 0 };
    const chosen = p.hand.find((s) => s.id === p.pick_id);
    // Every card opens its character's page.
    const hand = p.hand.map((s) => `<a class="dr-mini-wrap${s.id === p.pick_id ? ' picked' : ''}" href="character.html?id=${s.id}"
      title="${escapeHtml(shortName(s.name))}${s.form ? ` · ${escapeHtml(s.form)}` : ''}${s.id === p.pick_id ? ' (their pick)' : ''}${s.id === p.best_id ? ' (highest tier)' : ''}">
      <span class="dr-mini" style="background:${accentFor(s.id)}">${characterTileInner(s.name, s.image_url, 80)}</span>${s.id === p.best_id ? STAR : ''}</a>`).join('');
    const pts = p.points ? (p.points >= rivalsOf(player) ? 'max' : 'some') : '';
    return `<div class="dr-row${chosen ? '' : ' missed'}">
      <div class="dr-who">${avatar(player, `duel-pick-avatar${player.me ? ' me' : ''}`)}<div class="dr-who-text">
        <div class="dr-who-name${player.me ? ' me' : ''}">${escapeHtml(player.me ? 'you' : p.username)}</div>
        <div class="dr-who-pick">${chosen ? `${escapeHtml(shortName(chosen.name))}${chosen.form ? ` · ${escapeHtml(chosen.form)}` : ''}` : 'no pick'}</div></div></div>
      <div class="dr-hand">${hand}</div>
      <span class="dr-points ${pts}">+${p.points}</span>
    </div>`;
  };
  // A round's head-to-heads: each opens the matchup on Compare.
  const who = (name) => escapeHtml(byName[name] && byName[name].me ? 'you' : name);
  const boutSide = (side, user, won) => `<span class="dr-bout-side${won ? ' won' : ''}">${
    side ? escapeHtml(shortName(side.name)) : `<span class="dr-bout-none">${who(user)}: no pick</span>`}</span>`;
  const boutHtml = (b) => {
    const inner = `${boutSide(b.a, b.user_a, b.winner && b.winner === b.user_a)}<span class="duel-vs">vs</span>${boutSide(b.b, b.user_b, b.winner && b.winner === b.user_b)}${
      b.by_speed ? '<span class="dr-bout-note" title="Dead even on stats: the faster pick won">faster pick</span>'
        : !b.winner && b.a && b.b ? '<span class="dr-bout-note">even</span>' : ''}`;
    return b.compare_url ? `<a class="dr-bout" href="${escapeHtml(b.compare_url)}">${inner}</a>` : `<span class="dr-bout">${inner}</span>`;
  };
  if (g.mode === 'gauntlet') {
    const guessRow = (p) => {
      const player = byName[p.username] || { username: p.username };
      const pts = p.pick_id == null ? 'none' : ['none', 'one', 'two', 'three'][p.points];
      return `<div class="gd-guess-row${p.pick_id == null ? ' missed' : ''}">
        <span class="gd-guess-who">${avatar(player, `duel-pick-avatar${player.me ? ' me' : ''}`)}
          <span class="gd-guess-name${player.me ? ' me' : ''}">${escapeHtml(player.me ? 'you' : p.username)}</span></span>
        <span class="gd-guess-said">${p.pick_id == null ? 'No guess' : `Guessed ${p.pick_id}`}</span>
        <span class="gd-points ${pts}">+${p.points}</span></div>`;
    };
    return `
    <div class="duel-result-head ${outcome}">
      <div class="duel-result-kicker">${tagText(g)} · Finished</div>
      <button type="button" class="duel-result-share pill-button btn-sm">Share ▾</button>
      <div class="duel-result-title">${title}</div>
      <div class="duel-result-score">${score}</div>
      <div class="duel-result-people">${people}</div>
    </div>
    ${g.rounds.map((r) => `
      <section class="gd-round">
        <h2 class="gd-lbl gd-round-title">Round ${r.round_no}</h2>
        <div class="gd-result-card">
          <div class="gd-result-head">
            <a class="gd-result-hero" href="${escapeHtml(r.compare_url)}">${sideTileHtml(r.a, 160)}<span class="gd-hero-text">
              <span class="gd-hero-name">${escapeHtml(shortName(r.a.name))}${r.picked ? ' <span class="duel-picked">picked</span>' : ''}</span>
              <span class="gd-hero-sub">${escapeHtml(r.a.series)}${r.a.form ? ` · ${escapeHtml(r.a.form)}` : ''}</span></span></a>
            <span class="gd-climbed">Climbed ${r.answer_id} of ${r.fights.length}</span>
          </div>
          <div class="gd-result-ladder">${r.fights.map((f) => `
            <a class="gd-result-rung ${f.reached ? f.outcome : 'unreached'}" href="${escapeHtml(f.compare_url)}"
              title="${f.rung}. ${escapeHtml(shortName(f.opponent.name))}${f.opponent.form ? ` (${escapeHtml(f.opponent.form)})` : ''}: ${escapeHtml(f.reached ? f.verdict : 'not reached')}">
              ${sideTileHtml(f.opponent, 80)}</a>`).join('')}</div>
        </div>
        <div class="gd-guess-card">${r.picks.map(guessRow).join('')}</div>
      </section>`).join('')}
    <p class="dr-legend">Each ladder runs weakest to strongest; the run ends at the first loss or draw. 3 points for the exact number, 2 if one off, 1 if two off. Tap an opponent to open that matchup.</p>`;
  }
  if (g.mode === 'draft') {
    return `
    <div class="duel-result-head ${outcome}">
      <div class="duel-result-kicker">${tagText(g)} · Finished</div>
      <button type="button" class="duel-result-share pill-button btn-sm">Share ▾</button>
      <div class="duel-result-title">${title}</div>
      <div class="duel-result-score">${score}</div>
      <div class="duel-result-people">${people}</div>
    </div>
    ${g.rounds.map((r) => `
      <section class="dr-round">
        <h2 class="dr-round-title">Round ${r.round_no}</h2>
        <div class="dr-round-card">
          ${r.bouts.length ? `<div class="dr-bouts">${r.bouts.map(boutHtml).join('')}</div>` : ''}
          ${r.picks.map(draftRow).join('')}
        </div>
      </section>`).join('')}
    <p class="dr-legend">Gold ring: their pick. Star: the highest-tier character in their hand. Each pick earns a point for every opponent's pick it beats; when two picks are dead even on stats, the faster pick wins. Tap a matchup to open it, or a card for that character.</p>`;
  }
  return `
    <div class="duel-result-head ${outcome}">
      <div class="duel-result-kicker">${tagText(g)} · Finished</div>
      <button type="button" class="duel-result-share pill-button btn-sm">Share ▾</button>
      <div class="duel-result-title">${title}</div>
      <div class="duel-result-score">${score}</div>
      <div class="duel-result-people">${people}</div>
    </div>
    <h2 class="duel-rounds-title">Round by round</h2>
    <ol class="duel-rounds">
      ${g.rounds.map((r) => `
        <li class="duel-round">
          <span class="duel-round-no">R${r.round_no}</span>
          <div class="duel-round-main">
            <a href="${escapeHtml(r.compare_url)}" class="duel-round-vs">${sideLabel(r.a)} <span class="duel-vs">vs</span> ${sideLabel(r.b)}</a>${r.picked ? '<span class="duel-picked">picked</span>' : ''}
            <div class="duel-round-verdict${r.verdict.includes('overruled') ? ' overruled' : ''}">${escapeHtml(r.verdict)}</div>
          </div>
          <div class="duel-round-picks">${r.picks.map((p) => pick(r, p)).join('')}</div>
        </li>`).join('')}
    </ol>`;
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
// The round screen: two characters and a countdown. The server starts a
// round's clock when it hands the round out (a pick's response already
// carries the next round), and the time left comes from there - reopening
// mid-round continues it.

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

function playFrame(inner, { close = true } = {}) {
  playBox.hidden = false;
  document.body.classList.add('duel-playing');
  playBox.innerHTML = `<div class="duel-play-inner">${close ? '<button type="button" class="duel-play-close" aria-label="Close">✕</button>' : ''}${inner}</div>`;
  const btn = playBox.querySelector('.duel-play-close');
  if (btn) btn.addEventListener('click', () => {
    const live = playBox.querySelector('.duel-choice:not(:disabled)');
    if (!live || confirm("Close? This round's clock keeps running.")) closePlay();
  });
}

function playTitle() {
  if (!playing) return '';
  const mine = playing.players.find((p) => p.me);
  const rivals = playing.players.filter((p) => !mine || p.team !== mine.team);
  const mates = playing.players.filter((p) => mine && !p.me && p.team === mine.team);
  const vs = rivals.length ? `You${mates.length ? ` &amp; ${names(mates)}` : ''} vs ${names(rivals)}` : `You · ${playing.format}, waiting for players`;
  return vs;
}

function loadingFrame(text) {
  playFrame(`<div class="duel-loading"><span class="duel-spinner"></span>${text}</div>`);
}

async function play(gameId, game = null) {
  playing = game || findGame(gameId);
  loadingFrame('Loading the round…');
  try {
    const { round } = await Api.nextRound(gameId);
    if (round) showRound(round);
    else await showFinished(gameId);
  } catch (e) {
    playFrame(`<div class="form-error">${escapeHtml(e.message)}</div>`);
  }
}

function gauntletRoundHtml(r) {
  const c = r.a;
  const form = (x) => (x.form ? `<span class="gd-rung-form"> · ${escapeHtml(x.form)}</span>` : '');
  return `<div class="gd-play">
    <div class="gd-top">
      <div class="gd-hero">${sideTileHtml(c, 320)}<div class="gd-hero-text">
        <div class="gd-hero-name">${escapeHtml(shortName(c.name))}</div>
        <div class="gd-hero-sub">${escapeHtml(c.series)}${c.form ? ` · ${escapeHtml(c.form)}` : ''}</div></div></div>
      <div class="gd-ladder-col">
        <div class="gd-lbl">The ladder</div>
        <ol class="gd-ladder" style="--rows:${Math.ceil(r.ladder.length / 2)}">${r.ladder.map((x, i) => `
          <li class="gd-rung"><span class="gd-rung-no">${i + 1}</span>${sideTileHtml(x, 64)}
            <span class="gd-rung-name">${escapeHtml(shortName(x.name))}${form(x)}</span></li>`).join('')}</ol>
      </div>
    </div>
    <div class="gd-lbl gd-prompt">How many does it beat before its first loss?</div>
    <div class="gd-guesses">${Array.from({ length: r.ladder.length + 1 }, (_, n) => `
      <button type="button" class="gd-num" data-pick="${n}">${n}</button>`).join('')}</div>
  </div>`;
}

function showRound(r) {
  const dots = Array.from({ length: r.total }, (_, i) => `<span class="duel-dot${i + 1 < r.round_no ? ' done' : i + 1 === r.round_no ? ' now' : ''}"></span>`).join('');
  const roundLength = r.mode === 'gauntlet' ? 30 : 20;
  playFrame(`
    <div class="duel-play-round" id="play-title">Round ${r.round_no} of ${r.total}${r.mode === 'draft' ? ' — pick your fighter' : r.mode === 'gauntlet' ? ' — how far does it climb?' : ''}</div>
    <div class="duel-play-sub">${playTitle()}</div>
    <div class="duel-dots" aria-hidden="true">${dots}</div>
    <div class="duel-clock"><span class="duel-clock-num" aria-live="off"></span><div class="duel-clock-track"><div class="duel-clock-bar"></div></div></div>
    ${r.mode === 'draft' ? `<div class="dr-prompt">Your pick fights each opponent's: take the strongest of your ${r.hand.length}</div>` : ''}
    ${r.mode === 'gauntlet' ? gauntletRoundHtml(r) : `<div class="duel-choices${r.mode === 'draft' ? ' dr-hand-grid' : ''}">
      ${(r.mode === 'draft' ? r.hand : [r.a, r.b]).map((s) => `
        <button type="button" class="duel-choice" data-pick="${s.id}">
          <span class="duel-choice-check">${CHECK(18)}</span>
          ${sideTileHtml(s, 320)}
          <span class="duel-choice-name">${escapeHtml(shortName(s.name))}</span>
          ${s.form ? `<span class="duel-choice-form">${escapeHtml(s.form)}</span>` : ''}
          <span class="duel-choice-series">${escapeHtml(s.series)}</span>
        </button>`).join(r.mode === 'draft' ? '' : '<span class="duel-choice-vs">vs</span>')}
    </div>`}
    <div class="duel-play-status" aria-live="polite"></div>`);
  playBox.querySelectorAll('.duel-choice-check').forEach((c) => { c.style.color = 'var(--accent-gold)'; });
  const bar = playBox.querySelector('.duel-clock-bar');
  const num = playBox.querySelector('.duel-clock-num');
  const status = playBox.querySelector('.duel-play-status');
  const endAt = performance.now() + r.seconds_left * 1000;
  let locked = false;
  const lock = () => {
    locked = true;
    clearInterval(timer);
    playBox.querySelectorAll('[data-pick]').forEach((b) => { b.disabled = true; });
  };
  const next = (res) => (res && res.next ? showRound(res.next) : showFinished(r.game_id));

  const tick = () => {
    const left = Math.max(0, (endAt - performance.now()) / 1000);
    bar.style.transform = `scaleX(${left / roundLength})`;
    bar.classList.toggle('urgent', left <= 5);
    num.classList.toggle('urgent', left <= 5);
    num.textContent = Math.ceil(left);
    if (left <= 0 && !locked) {
      lock();
      status.className = 'duel-play-status bad';
      status.textContent = "Time's up — no pick recorded";
      setTimeout(() => play(r.game_id), 1100);
    }
  };
  clearInterval(timer);
  timer = setInterval(tick, 100);
  tick();

  playBox.querySelectorAll('[data-pick]').forEach((btn) => btn.addEventListener('click', async () => {
    if (locked) return;
    lock();
    btn.classList.add('chosen');
    const who = r.mode === 'draft' ? `${btn.querySelector('.duel-choice-name').textContent} locked in`
      : r.mode === 'gauntlet' ? `${btn.dataset.pick} locked in` : 'Locked in';
    status.textContent = r.round_no < r.total ? who : `${who} — that was the last one`;
    try {
      const res = await Api.pickRound(r.game_id, r.round_no, Number(btn.dataset.pick));
      if (!res.in_time) { status.className = 'duel-play-status bad'; status.textContent = 'Too late — this round counts as wrong'; }
      // The next round is already in the response - its clock started
      // with it, so show it straight away.
      setTimeout(() => next(res), res.in_time ? 300 : 1000);
    } catch (e) {
      status.className = 'duel-play-status bad';
      status.textContent = e.message;
      setTimeout(() => play(r.game_id), 1000);
    }
  }));
}

async function showFinished(gameId) {
  clearInterval(timer);
  loadingFrame('Wrapping up…');
  let g = null;
  try { g = await Api.getGame(gameId); } catch { g = playing; }
  if (g && g.status === 'done') {
    playFrame(`<div class="duel-play-end">${resultsHtml(g)}</div><button type="button" class="btn-gold duel-done-btn">Done</button>`);
    wireResultShare(playBox, g);
  } else {
    const others = g ? g.players.filter((p) => !p.me && p.played < g.total) : [];
    const wait = g && g.status === 'open'
      ? 'Results show once every seat is filled and everyone has played.'
      : `Results show once ${others.length ? names(others) : 'everyone'} ${others.length === 1 ? 'has' : 'have'} played.`;
    playFrame(`<div class="duel-play-round" id="play-title">You're all done</div>
      <div class="duel-play-sub">All ${g ? g.total : 5} locked in. ${wait}</div>
      <button type="button" class="btn-gold duel-done-btn">Done</button>`);
  }
  playBox.querySelector('.duel-done-btn').addEventListener('click', () => {
    closePlay();
    if (g) openGame(g.id);
  });
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !playBox.hidden && !playBox.querySelector('[data-pick]:not(:disabled)')) closePlay();
});

// --- start -----------------------------------------------------------------------------

(async () => {
  const cats = Api.listCategories().catch(() => []);
  me = await currentUser();
  series = (await cats).map((c) => c.name).filter((n) => n !== 'Uncategorized');
  renderNew();
  // A shared link (duels.html?game=ID) opens that game's page first.
  const id = Number(new URLSearchParams(location.search).get('game'));
  if (id) openGame(id, { push: false });
  renderLeaderboard();
  await refresh();
  liveCheck();
})();
