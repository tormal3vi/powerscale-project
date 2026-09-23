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

// "HIGH 3-A" -> "High 3-A", as the design writes tiers.
function tierText(c) {
  if (!c.tier_label) return '—';
  return c.tier_label.toLowerCase()
    .replace(/\b(high|low)\b/, (w) => w[0].toUpperCase() + w.slice(1))
    .replace(/\d+-[abc]/, (code) => code.toUpperCase());
}

// --- setup ------------------------------------------------------------------

function renderResults() {
  const q = fold(searchInput.value.trim());
  const box = $('t-results');
  box.innerHTML = '';
  if (!q) {
    box.innerHTML = '<div class="t-empty">Type a name to add characters, or use "Fill randomly".</div>';
    return;
  }
  const pickedIds = new Set(state.picked.map((c) => c.id));
  const matches = state.all
    .filter((c) => c.scorable && !pickedIds.has(c.id) && (fold(c.name).includes(q) || fold(c.aliases).includes(q)))
    .slice(0, 12);
  if (!matches.length) {
    box.innerHTML = '<div class="t-empty">No matches with enough data for a verdict.</div>';
    return;
  }
  for (const c of matches) {
    const row = document.createElement('button');
    row.className = 't-result';
    row.innerHTML = `<span class="t-result-name"><strong></strong> <span></span></span><span class="t-tier"></span>`;
    row.querySelector('strong').textContent = c.name;
    row.querySelector('.t-result-name > span').textContent = `· ${c.category}`;
    row.querySelector('.t-tier').textContent = tierText(c);
    row.disabled = state.picked.length >= state.size;
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
    const row = document.createElement('div');
    row.className = 't-entrant';
    row.innerHTML = `<span class="t-seed">${i + 1}</span><span class="t-entrant-name"></span><button class="t-remove">×</button>`;
    row.querySelector('.t-entrant-name').textContent = c.name;
    const remove = row.querySelector('.t-remove');
    remove.setAttribute('aria-label', `Remove ${c.name}`);
    remove.addEventListener('click', () => {
      state.picked.splice(i, 1);
      renderPicked();
      renderResults();
    });
    list.appendChild(row);
  });
  $('t-picked-label').textContent = `Entrants (${state.picked.length}/${state.size})`;
  $('run-tournament').disabled = state.picked.length !== state.size;
}

function setSize(size) {
  state.size = size;
  state.picked = state.picked.slice(0, size);
  document.querySelectorAll('.t-seg button').forEach((b) => b.classList.toggle('active', Number(b.dataset.size) === size));
  renderPicked();
  renderResults();
}

document.querySelectorAll('.t-seg button').forEach((b) => b.addEventListener('click', () => setSize(Number(b.dataset.size))));
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

// Decide one match. An admin overrule beats the calculator; otherwise the
// favored side wins, and a "too close to call" or no-data result goes to
// the higher Tier, then to the higher seed.
async function playMatch(a, b) {
  let v = null;
  try { v = await Api.compare(a.id, b.id); } catch { /* scored below as a tiebreak */ }
  if (v && v.override) {
    const winner = v.override.winner_id === a.id ? a : b;
    return { a, b, winner, how: 'Overruled by admins' };
  }
  if (v && v.composite !== null && v.composite !== 0) {
    return { a, b, winner: v.composite > 0 ? a : b, how: v.label };
  }
  const ta = a.tier_score ?? -Infinity;
  const tb = b.tier_score ?? -Infinity;
  const winner = tb > ta ? b : a;
  return { a, b, winner, how: tb === ta ? 'Tiebreak: higher seed' : 'Tiebreak: higher Tier' };
}

function matchHtml(m, isFinal) {
  const side = (c) => `
    <div class="t-side ${m.winner.id === c.id ? 'won' : 'lost'}">
      <span class="t-avatar" style="background:${accentFor(c.id)}"></span>
      <span class="t-name">${escapeHtml(shortName(c.name))}</span>
      <span class="t-tier">${escapeHtml(tierText(c))}</span>
    </div>`;
  return `
    <a class="t-match${isFinal ? ' t-final' : ''}" href="compare.html?a=${m.a.id}&b=${m.b.id}" title="Open this matchup">
      ${side(m.a)}${side(m.b)}
      <div class="t-how">${escapeHtml(m.how)}</div>
    </a>`;
}

function renderBracket(rounds, total) {
  const totalRounds = Math.log2(total);
  const names = { 1: 'Final', 2: 'Semifinals', 4: 'Quarterfinals' };
  $('bracket').innerHTML = rounds.map((matches, r) => `
    <div class="t-round">
      <div class="t-round-label">${names[total / 2 ** (r + 1)] || `Round ${r + 1}`}</div>
      <div class="t-round-matches">${matches.map((m) => matchHtml(m, r === totalRounds - 1)).join('')}</div>
    </div>`).join('');
}

function setChampionCard(label, name, avatarChar) {
  $('t-champ-label').textContent = label;
  $('t-champ-name').textContent = name;
  $('t-champ-avatar').textContent = avatarChar;
}

async function runTournament(entrants) {
  $('setup').style.display = 'none';
  $('bracket-wrap').style.display = '';
  $('bracket').innerHTML = '';
  const totalRounds = Math.log2(entrants.length);
  const rounds = [];
  let alive = entrants.slice();
  let round = 1;
  while (alive.length > 1) {
    setChampionCard('In progress', `Round ${round} of ${totalRounds}…`, '…');
    const pairs = [];
    for (let i = 0; i < alive.length; i += 2) pairs.push([alive[i], alive[i + 1]]);
    const results = await Promise.all(pairs.map(([a, b]) => playMatch(a, b)));
    rounds.push(results);
    renderBracket(rounds, entrants.length);
    alive = results.map((m) => m.winner);
    round += 1;
  }
  const champ = alive[0];
  setChampionCard('Champion', shortName(champ.name), initialFor(champ.name));
}

// --- load ---------------------------------------------------------------------

async function init() {
  const res = await Api.listCharacters();
  state.all = res.characters;
  for (const c of state.all) state.byId.set(c.id, c);
  const ids = (new URLSearchParams(location.search).get('ids') || '').split(',').map(Number).filter(Boolean);
  const entrants = ids.map((id) => state.byId.get(id)).filter(Boolean);
  if ((entrants.length === 8 || entrants.length === 16) && entrants.length === ids.length) {
    setSize(entrants.length);
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
