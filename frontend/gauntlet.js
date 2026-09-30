// Gauntlet: one character against a ladder of ever-stronger opponents,
// revealed fight by fight (backend/gauntlet.py works it out). Everything
// that shapes a run lives in the URL, so a link replays the same run.
renderTopbar([]);

const $ = (id) => document.getElementById(id);
const MAX_PICKS = 10;
const params = new URLSearchParams(location.search);
const state = {
  char: null, // {id, name, image_url, ...} from the roster, or null: random
  source: ['random', 'series', 'custom'].includes(params.get('source')) ? params.get('source') : 'random',
  series: params.get('series') || '',
  picks: [], // custom opponents
};
const HINTS = {
  random: 'Ten opponents, one from each tenth of the whole roster by Tier: rung 1 is among the weakest characters on the site, rung 10 among the strongest.',
  series: "Up to ten of that series' best-known characters, weakest first.",
  custom: 'Up to ten characters you pick. They fight in order of Tier, weakest first.',
};

function tile(c, px, cls = '') {
  return `<span class="gl-tile ${cls}${c.image_url ? ' has-pic' : ''}" style="background:${accentFor(c.id)}">${characterTileInner(c.name, c.image_url, px)}</span>`;
}

// --- setup -----------------------------------------------------------------------

function renderChar() {
  const slot = $('gl-char');
  if (state.char) {
    slot.innerHTML = `<span class="fav-chip">${tile(state.char, 52, 'fav-chip-tile')}<span class="fav-chip-name"></span>
      <button type="button" class="fav-chip-clear" aria-label="Change character">×</button></span>`;
    slot.querySelector('.fav-chip-name').textContent = shortName(state.char.name);
    slot.querySelector('.fav-chip-clear').addEventListener('click', () => { state.char = null; renderChar(); slot.querySelector('input').focus(); });
    return;
  }
  slot.innerHTML = '';
  slot.appendChild(characterSearchEl({
    placeholder: 'Any character… (or leave it to chance)',
    label: 'Character',
    onChoose: (c) => { state.char = c; renderChar(); },
  }));
}

function renderSource() {
  document.querySelectorAll('[data-src]').forEach((b) => {
    const on = b.dataset.src === state.source;
    b.classList.toggle('active', on);
    b.setAttribute('aria-checked', String(on));
  });
  $('gl-src-hint').textContent = HINTS[state.source];
  $('gl-series').hidden = state.source !== 'series';
  $('gl-custom').hidden = state.source !== 'custom';
}

function renderPicks() {
  $('gl-picks').innerHTML = state.picks.map((c, i) => `
    <span class="fav-chip gl-pick">${tile(c, 52, 'fav-chip-tile')}<span class="fav-chip-name">${escapeHtml(shortName(c.name))}</span>
      <button type="button" class="fav-chip-clear" data-i="${i}" aria-label="Remove ${escapeHtml(shortName(c.name))}">×</button></span>`).join('');
  $('gl-custom-search').hidden = state.picks.length >= MAX_PICKS;
}

document.querySelectorAll('[data-src]').forEach((b) => b.addEventListener('click', () => {
  state.source = b.dataset.src;
  renderSource();
}));
$('gl-series').addEventListener('change', (e) => { state.series = e.target.value; });
$('gl-picks').addEventListener('click', (e) => {
  const x = e.target.closest('[data-i]');
  if (!x) return;
  state.picks.splice(Number(x.dataset.i), 1);
  renderPicks();
});
$('gl-custom-search').appendChild(characterSearchEl({
  placeholder: 'Add an opponent…',
  label: 'Add an opponent',
  exclude: () => state.picks.map((c) => c.id),
  onChoose: (c) => { if (state.picks.length < MAX_PICKS) state.picks.push(c); renderPicks(); },
}));
$('gl-random-char').addEventListener('click', () => { state.char = null; renderChar(); run({ fresh: true }); });
$('gl-run').addEventListener('click', () => run({ fresh: true }));

// --- running ------------------------------------------------------------------------

let revealTimer = null;

async function run({ fresh = false, seed = null } = {}) {
  $('gl-error').textContent = '';
  const q = { source: state.source };
  if (state.char) q.char = state.char.id;
  if (state.source === 'series') {
    if (!state.series) { $('gl-error').textContent = 'Pick a series.'; return; }
    q.series = state.series;
  }
  if (state.source === 'custom') {
    if (state.picks.length < 2) { $('gl-error').textContent = 'Add at least two opponents.'; return; }
    q.opponents = state.picks.map((c) => c.id).join(',');
  }
  if (seed && !fresh) q.seed = seed;
  const btn = $('gl-run');
  btn.disabled = true;
  btn.textContent = 'Building the ladder…';
  try {
    const r = await Api.runGauntlet(q);
    // The URL replays this exact run: the character and, for a random
    // ladder, the seed that drew it.
    const url = new URLSearchParams({ char: r.character.id, source: r.source });
    if (r.series) url.set('series', r.series);
    if (q.opponents) url.set('opponents', q.opponents);
    if (r.seed) url.set('seed', r.seed);
    history.replaceState(null, '', `gauntlet.html?${url}`);
    showResult(r);
  } catch (e) {
    $('gl-error').textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run the gauntlet';
  }
}

const OUTCOME = { win: 'Win', loss: 'Loss', even: 'Too close', none: 'No verdict' };

function showResult(r) {
  clearTimeout(revealTimer);
  const box = $('gl-result');
  const c = r.character;
  const name = shortName(c.name);
  box.hidden = false;
  box.innerHTML = `
    <div class="gl-hero">
      ${tile(c, 160, 'gl-hero-tile')}
      <div class="gl-hero-text">
        <a class="gl-hero-name" href="character.html?id=${c.id}"></a>
        <div class="gl-hero-sub">${escapeHtml(c.series)}${c.form ? ` · ${escapeHtml(c.form)}` : ''}</div>
        <div class="gl-score" id="gl-score">Climbing…</div>
      </div>
      <div class="gl-hero-actions">
        <button type="button" class="pill-button" id="gl-again">${r.source === 'random' ? 'New ladder' : 'Run again'}</button>
        <button type="button" class="pill-button" id="gl-share">Copy link</button>
      </div>
    </div>
    <ol class="gl-ladder">${r.fights.map((f) => `
      <li class="gl-rung pending ${f.outcome}${f.reached ? '' : ' unreached'}">
        <span class="gl-rung-no">${f.rung}</span>
        ${tile(f.opponent, 96)}
        <span class="gl-rung-text">
          <span class="gl-rung-name">${escapeHtml(shortName(f.opponent.name))}</span>
          <span class="gl-rung-sub">${escapeHtml(f.opponent.series)}</span>
        </span>
        <a class="gl-rung-result" href="${escapeHtml(f.compare_url)}" title="${escapeHtml(f.verdict)}">
          <span class="gl-badge">${f.reached ? OUTCOME[f.outcome] : 'Not reached'}</span>
          <span class="gl-verdict">${escapeHtml(f.verdict)}</span>
        </a>
      </li>`).join('')}
    </ol>`;
  box.querySelector('.gl-hero-name').textContent = name;
  box.querySelector('#gl-again').addEventListener('click', () => run({ fresh: true }));
  const share = box.querySelector('#gl-share');
  share.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(location.href); share.textContent = 'Link copied'; } catch { share.textContent = 'Copy failed'; }
    setTimeout(() => { share.textContent = 'Copy link'; }, 1800);
  });
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // One fight at a time, up to where the run ended; then the rest at once.
  const rungs = [...box.querySelectorAll('.gl-rung')];
  const lastReached = rungs.findIndex((el) => el.classList.contains('unreached'));
  const stop = lastReached < 0 ? rungs.length : lastReached;
  let i = 0;
  const step = () => {
    if (i < stop) {
      rungs[i].classList.remove('pending');
      i += 1;
      revealTimer = setTimeout(step, 520);
      return;
    }
    rungs.slice(stop).forEach((el) => el.classList.remove('pending'));
    const all = r.climbed === r.total;
    box.querySelector('#gl-score').innerHTML = all
      ? `Cleared all <b>${r.total}</b>`
      : `Climbed <b>${r.climbed}</b> of ${r.total}`;
    box.querySelector('.gl-hero').classList.add(all ? 'cleared' : 'done');
  };
  step();
}

// --- start ------------------------------------------------------------------------------

(async () => {
  renderSource();
  renderPicks();
  const [cats, all] = await Promise.all([Api.listCategories().catch(() => []), roster().catch(() => [])]);
  $('gl-series').innerHTML = '<option value="">Pick a series…</option>'
    + cats.map((c) => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join('');
  $('gl-series').value = state.series;
  const byId = new Map(all.map((c) => [c.id, c]));
  const charId = Number(params.get('char'));
  state.char = byId.get(charId) || null;
  state.picks = (params.get('opponents') || '').split(',').map(Number).map((id) => byId.get(id)).filter(Boolean).slice(0, MAX_PICKS);
  renderChar();
  renderPicks();
  // A shared link (or the character page's button): run it straight away.
  if (state.char) run({ seed: params.get('seed') });
})();
