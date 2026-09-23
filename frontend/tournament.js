// Single-elimination tournament run by the same calculator as the compare
// page (/api/compare, default forms). tournament.html?ids=1,2,... replays
// a bracket straight away, so a finished bracket is shareable as a link.

renderTopbar([]);

const state = {
  all: [], // character summaries from /api/characters
  byId: new Map(),
  picked: [], // entrant summaries, in seed order
  size: 8,
};

const $ = (id) => document.getElementById(id);
const searchInput = $('t-search');

function fold(s) {
  return (s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

// --- setup ------------------------------------------------------------------

function renderResults() {
  const q = fold(searchInput.value.trim());
  const box = $('t-results');
  box.innerHTML = '';
  if (!q) {
    box.innerHTML = '<div class="char-stat-raw">Type a name, or use "Fill randomly".</div>';
    return;
  }
  const pickedIds = new Set(state.picked.map((c) => c.id));
  const matches = state.all
    .filter((c) => c.scorable && !pickedIds.has(c.id) && (fold(c.name).includes(q) || fold(c.aliases).includes(q)))
    .slice(0, 12);
  if (!matches.length) {
    box.innerHTML = '<div class="char-stat-raw">No matches with enough data for a verdict.</div>';
    return;
  }
  for (const c of matches) {
    const row = document.createElement('button');
    row.className = 't-result';
    row.innerHTML = `<span class="t-name"></span><span class="t-meta"></span>`;
    row.querySelector('.t-name').textContent = c.name;
    row.querySelector('.t-meta').textContent = `${c.category} · ${c.tier_label || '—'}`;
    row.addEventListener('click', () => addEntrant(c));
    box.appendChild(row);
  }
}

function addEntrant(c) {
  if (state.picked.length >= state.size) return;
  state.picked.push(c);
  renderPicked();
  renderResults();
}

function renderPicked() {
  const list = $('t-picked');
  list.innerHTML = '';
  state.picked.forEach((c, i) => {
    const li = document.createElement('li');
    li.innerHTML = `<span class="t-name"></span><span class="t-meta"></span><button class="t-remove" aria-label="Remove">×</button>`;
    li.querySelector('.t-name').textContent = c.name;
    li.querySelector('.t-meta').textContent = c.tier_label || '—';
    li.querySelector('.t-remove').addEventListener('click', () => {
      state.picked.splice(i, 1);
      renderPicked();
      renderResults();
    });
    list.appendChild(li);
  });
  $('t-picked-label').textContent = `Entrants (${state.picked.length}/${state.size}) — seeded in this order`;
  $('run-tournament').disabled = state.picked.length !== state.size;
}

$('size-select').addEventListener('change', (e) => {
  state.size = Number(e.target.value);
  state.picked = state.picked.slice(0, state.size);
  renderPicked();
});
searchInput.addEventListener('input', renderResults);
$('clear-all').addEventListener('click', () => { state.picked = []; renderPicked(); renderResults(); });
$('fill-random').addEventListener('click', () => {
  const pickedIds = new Set(state.picked.map((c) => c.id));
  const pool = state.all.filter((c) => c.scorable && !pickedIds.has(c.id));
  while (state.picked.length < state.size && pool.length) {
    state.picked.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
  }
  renderPicked();
  renderResults();
});
$('run-tournament').addEventListener('click', () => {
  history.replaceState(null, '', `tournament.html?ids=${state.picked.map((c) => c.id).join(',')}`);
  runTournament(state.picked);
});
$('new-tournament').addEventListener('click', () => {
  history.replaceState(null, '', 'tournament.html');
  $('bracket-wrap').style.display = 'none';
  $('setup').style.display = '';
});
$('copy-bracket').addEventListener('click', async (e) => {
  try { await navigator.clipboard.writeText(location.href); e.target.textContent = 'Link copied'; } catch { e.target.textContent = 'Copy failed'; }
  setTimeout(() => { e.target.textContent = 'Copy link'; }, 1800);
});

// --- running the bracket -------------------------------------------------------

// Decide one match. An admin overrule (once accounts exist) beats the
// calculator; otherwise the favored side wins, and a "too close to call"
// or no-data result goes to the higher Tier, then to the higher seed.
async function playMatch(a, b) {
  let v = null;
  try { v = await Api.compare(a.id, b.id); } catch { /* scored below as a tiebreak */ }
  if (v && v.override) {
    const winner = v.override.winner_id === a.id ? a : b;
    return { a, b, winner, verdict: v, how: 'Overruled by admins' };
  }
  if (v && v.composite !== null && v.composite !== 0) {
    return { a, b, winner: v.composite > 0 ? a : b, verdict: v, how: v.label };
  }
  const ta = a.tier_score ?? -Infinity;
  const tb = b.tier_score ?? -Infinity;
  const winner = tb > ta ? b : a;
  return { a, b, winner, verdict: v, how: tb === ta ? 'Tiebreak: higher seed' : 'Tiebreak: higher Tier' };
}

function matchHtml(m) {
  const side = (c) => `
    <div class="t-side ${m && m.winner.id === c.id ? 'won' : 'lost'}">
      <span class="t-avatar" style="background:${accentFor(c.id)}">${escapeHtml(initialFor(c.name))}</span>
      <span class="t-name">${escapeHtml(c.name)}</span>
      <span class="t-meta">${escapeHtml(c.tier_label || '—')}</span>
    </div>`;
  return `
    <a class="t-match" href="compare.html?a=${m.a.id}&b=${m.b.id}" title="Open this matchup">
      ${side(m.a)}${side(m.b)}
      <div class="t-how">${escapeHtml(m.how)}</div>
    </a>`;
}

function renderBracket(rounds, total) {
  const names = { 1: 'Final', 2: 'Semifinals', 4: 'Quarterfinals' };
  $('bracket').innerHTML = rounds.map((matches, r) => `
    <div class="t-round">
      <div class="section-label">${names[total / 2 ** (r + 1)] || `Round ${r + 1}`}</div>
      <div class="t-round-matches">${matches.map(matchHtml).join('')}</div>
    </div>`).join('');
}

async function runTournament(entrants) {
  $('setup').style.display = 'none';
  $('bracket-wrap').style.display = '';
  const rounds = [];
  let alive = entrants.slice();
  let round = 1;
  while (alive.length > 1) {
    $('t-status').textContent = `Running round ${round}…`;
    const pairs = [];
    for (let i = 0; i < alive.length; i += 2) pairs.push([alive[i], alive[i + 1]]);
    const results = await Promise.all(pairs.map(([a, b]) => playMatch(a, b)));
    rounds.push(results);
    renderBracket(rounds, entrants.length);
    alive = results.map((m) => m.winner);
    round += 1;
  }
  const champ = alive[0];
  $('t-status').innerHTML = `Champion: <strong></strong>`;
  $('t-status').querySelector('strong').textContent = champ.name;
}

// --- load ---------------------------------------------------------------------

async function init() {
  const res = await Api.listCharacters();
  state.all = res.characters;
  for (const c of state.all) state.byId.set(c.id, c);
  const ids = (new URLSearchParams(location.search).get('ids') || '').split(',').map(Number).filter(Boolean);
  const entrants = ids.map((id) => state.byId.get(id)).filter(Boolean);
  if ((entrants.length === 8 || entrants.length === 16) && entrants.length === ids.length) {
    state.size = entrants.length;
    $('size-select').value = String(state.size);
    state.picked = entrants;
    renderPicked();
    runTournament(entrants);
    return;
  }
  renderPicked();
  renderResults();
}

init().catch((err) => {
  $('setup').innerHTML = `<div class="error-state">Failed to load: ${escapeHtml(err.message)}</div>`;
});
