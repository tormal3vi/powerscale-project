// Gauntlet: one character against a ladder of ever-stronger opponents,
// revealed fight by fight (backend/gauntlet.py works it out). Everything
// that shapes a run lives in the URL, so a link replays the same run.
renderTopbar([]);

const $ = (id) => document.getElementById(id);
const MAX_PICKS = 10;
const params = new URLSearchParams(location.search);
const state = {
  char: null, // {id, name, image_url, ...} from the roster, or null: random
  forms: [], // the chosen character's form names (2+: a picker shows)
  form: params.get('form') || '',
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

// A character with several forms fights in its strongest unless you pick one.
async function loadForms() {
  const select = $('gl-form');
  select.hidden = true;
  state.forms = [];
  if (!state.char) { state.form = ''; return; }
  const id = state.char.id;
  try {
    const c = await Api.getCharacter(id);
    if (!state.char || state.char.id !== id) return;
    state.forms = c.forms.map((f) => f.name);
    const strongest = c.forms[defaultFormIndex(c.forms)].name;
    if (!state.forms.includes(state.form)) state.form = strongest;
    select.innerHTML = c.forms.map((f) => `<option value="${escapeHtml(f.name)}">${escapeHtml(f.name)}${f.name === strongest ? ' (strongest)' : ''}</option>`).join('');
    select.value = state.form;
    select.hidden = c.forms.length < 2;
  } catch { /* the run uses the strongest form */ }
}

function renderChar() {
  const slot = $('gl-char');
  loadForms();
  if (state.char) {
    slot.innerHTML = `<span class="fav-chip">${tile(state.char, 52, 'fav-chip-tile')}<span class="fav-chip-name"></span>
      <button type="button" class="fav-chip-clear" aria-label="Change character">×</button></span>`;
    slot.querySelector('.fav-chip-name').textContent = shortName(state.char.name);
    slot.querySelector('.fav-chip-clear').addEventListener('click', () => { state.char = null; state.form = ''; renderChar(); slot.querySelector('input').focus(); });
    return;
  }
  slot.innerHTML = '';
  slot.appendChild(characterSearchEl({
    placeholder: 'Any character… (or leave it to chance)',
    label: 'Character',
    onChoose: (c) => { state.char = c; state.form = ''; renderChar(); },
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
$('gl-form').addEventListener('change', (e) => { state.form = e.target.value; });
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
$('gl-random-char').addEventListener('click', () => { state.char = null; state.form = ''; renderChar(); run({ fresh: true }); });
$('gl-run').addEventListener('click', () => run({ fresh: true }));

// --- running ------------------------------------------------------------------------

let revealTimer = null;

async function run({ fresh = false, seed = null } = {}) {
  $('gl-error').textContent = '';
  const q = { source: state.source };
  if (state.char) q.char = state.char.id;
  if (state.char && state.form && state.forms.length !== 1) q.form = state.form; // (a shared link's form counts before the list loads)
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
    if (q.form) url.set('form', q.form);
    if (r.series) url.set('series', r.series);
    if (q.opponents) url.set('opponents', q.opponents);
    if (r.seed) url.set('seed', r.seed);
    history.replaceState(null, '', `gauntlet.html?${url}`);
    // A random character: now it's the chosen one (so "Run again" and the form picker work).
    if (!state.char || state.char.id !== r.character.id) {
      state.char = (await roster()).find((c) => c.id === r.character.id) || null;
      renderChar();
    }
    showResult(r);
  } catch (e) {
    $('gl-error').textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run the gauntlet';
  }
}

const OUTCOME = { win: 'Win', loss: 'Loss', even: 'Too close', none: 'No verdict' };
let lastRun = null;

// "Kirby favored — Clear favorite" -> "Clear favorite" (the badge says who won);
// "X wins — overruled by admins" -> "Overruled by admins".
function shortVerdict(v) {
  const tail = v.split(' — ').pop();
  return tail.charAt(0).toUpperCase() + tail.slice(1);
}

function nameWithForm(side) {
  return `${escapeHtml(shortName(side.name))}${side.form ? `<span class="gl-form-tag"> · ${escapeHtml(side.form)}</span>` : ''}`;
}

function showResult(r) {
  lastRun = r;
  clearTimeout(revealTimer);
  const box = $('gl-result');
  const c = r.character;
  box.hidden = false;
  box.innerHTML = `
    <div class="gl-hero">
      <div class="gl-hero-id">
        ${tile(c, 160, 'gl-hero-tile')}
        <div class="gl-hero-text">
          <a class="gl-hero-name" href="character.html?id=${c.id}"></a>
          <div class="gl-hero-sub">${escapeHtml(c.series)}${c.form ? ` · ${escapeHtml(c.form)}` : ''}</div>
        </div>
      </div>
      <div class="gl-hero-score">
        <div class="gl-lbl gl-score-lbl">Result</div>
        <div class="gl-score" id="gl-score">Climbing…</div>
      </div>
    </div>
    <ol class="gl-ladder">${r.fights.map((f, i) => `
      <li class="gl-rung pending ${f.outcome}${f.reached ? '' : ' unreached'}${f.reached && f.outcome !== 'win' ? ' stop' : ''}">
        <span class="gl-rung-no">${f.rung}</span>
        ${tile(f.opponent, 64)}
        <a class="gl-rung-text" href="${escapeHtml(f.compare_url)}" title="Open ${escapeHtml(shortName(c.name))} vs ${escapeHtml(shortName(f.opponent.name))}">
          <span class="gl-rung-name">${nameWithForm(f.opponent)}</span><span class="gl-rung-sub"> · ${escapeHtml(f.opponent.series)}</span>
        </a>
        <span class="gl-verdict" title="${escapeHtml(f.verdict)}">${f.reached ? escapeHtml(shortVerdict(f.verdict)) : ''}</span>
        <span class="gl-badge">${f.reached ? OUTCOME[f.outcome] : 'Not reached'}</span>
      </li>`).join('')}
    </ol>
    <div class="gl-actions">
      <button type="button" class="btn-gold" id="gl-again">Run again</button>
      ${r.source === 'random' ? '<button type="button" class="gl-btn" id="gl-new">New ladder</button>' : ''}
      <button type="button" class="gl-btn" id="gl-share">Share ▾</button>
    </div>`;
  box.querySelector('.gl-hero-name').textContent = shortName(c.name);
  box.querySelector('#gl-again').addEventListener('click', () => showResult(lastRun)); // the same fights, climbed again
  box.querySelector('#gl-new')?.addEventListener('click', () => run({ fresh: true }));
  const name = shortName(c.name);
  const result = r.climbed === r.total ? `cleared all ${r.total}` : `climbed ${r.climbed} of ${r.total}`;
  const image = (label, story) => ({ label, quiet: true, onClick: async (b, close) => {
    b.textContent = 'Drawing…';
    try { await saveGauntletImage(r, { story }); close(); } catch { b.textContent = "Couldn't make the image"; }
    setTimeout(() => { b.textContent = label; }, 1500);
  } });
  attachShareMenu(box.querySelector('#gl-share'), () => ({
    url: location.href,
    title: `How far does ${name} climb? The Powerscale gauntlet`,
    text: `${name} ${result} in the Powerscale gauntlet. How far would yours go?`,
  }), [image('Save as image', false), image('Save for TikTok / stories (9:16)', true)]);
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // One fight at a time, up to where the run ended; then the rest at once.
  const rungs = [...box.querySelectorAll('.gl-rung')];
  const firstUnreached = rungs.findIndex((el) => el.classList.contains('unreached'));
  const stop = firstUnreached < 0 ? rungs.length : firstUnreached;
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
    box.querySelector('#gl-score').textContent = all ? `Cleared all ${r.total}` : `Climbed ${r.climbed} of ${r.total}`;
    box.querySelector('.gl-hero').classList.add(all ? 'cleared' : 'done');
  };
  step();
}

// --- the share image ----------------------------------------------------------
// Square for posts, or 9:16 for TikTok, Shorts and stories: the character,
// how far it got, and the ladder (beaten green, the one it lost to red,
// the rest dimmed). Pictures come through this site's own proxy, so the
// canvas can be saved.
async function saveGauntletImage(r, { story = false } = {}) {
  const W = 1080;
  const H = story ? 1920 : 1080;
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const x = canvas.getContext('2d');
  await Promise.all(['700 64px Fraunces', '600 30px "Public Sans"', '600 26px "IBM Plex Mono"']
    .map((f) => document.fonts.load(f).catch(() => null)));
  const load = (src) => new Promise((res) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = () => res(null);
    img.src = src;
  });
  const pic = (side, px) => `api/characters/${side.id}/picture?px=${px}${side.form ? `&form=${encodeURIComponent(side.form)}` : ''}`;
  const c = r.character;
  const [hero, logo, ...foes] = await Promise.all([load(pic(c, 400)), load('favicon.svg?v=2'),
    ...r.fights.map((f) => load(pic(f.opponent, 200)))]);
  const text = (str, px, cx, y, { font = '"Public Sans"', weight = 600, color = '#F3EEE4', max = 900, spacing = 0 } = {}) => {
    let size = px;
    if ('letterSpacing' in x) x.letterSpacing = `${spacing}px`;
    do {
      x.font = `${weight} ${size}px ${font}, sans-serif`;
      size -= 2;
    } while (x.measureText(str).width > max && size > 14);
    x.fillStyle = color;
    x.textAlign = 'center';
    x.fillText(str, cx, y);
    if ('letterSpacing' in x) x.letterSpacing = '0px';
  };
  const tile = (img, side, left, top, size, radius, ring) => {
    x.save();
    x.beginPath();
    x.roundRect(left, top, size, size, radius);
    x.clip();
    x.fillStyle = accentFor(side.id);
    x.fillRect(left, top, size, size);
    if (img) x.drawImage(img, left, top, size, size);
    else text(initialFor(side.name), size * 0.42, left + size / 2, top + size * 0.64, { font: 'Fraunces', weight: 700, color: '#14120F' });
    x.restore();
    if (ring) {
      x.lineWidth = Math.max(4, size / 22);
      x.strokeStyle = ring;
      x.beginPath();
      x.roundRect(left, top, size, size, radius);
      x.stroke();
    }
  };
  x.fillStyle = '#14120F';
  x.fillRect(0, 0, W, H);
  const glow = x.createRadialGradient(W / 2, story ? 520 : 300, 0, W / 2, story ? 520 : 300, 640);
  glow.addColorStop(0, accentFor(c.id));
  glow.addColorStop(1, 'transparent');
  x.globalAlpha = 0.16;
  x.fillStyle = glow;
  x.fillRect(0, 0, W, H);
  x.globalAlpha = 1;

  const L = story
    ? { head: 200, hero: [W / 2 - 180, 280, 360, 56], name: 740, sub: 790, result: 920, cols: 5, thumb: 132, gap: 24, ladder: 990 }
    : { head: 100, hero: [W / 2 - 120, 140, 240, 40], name: 450, sub: 492, result: 590, cols: 10, thumb: 80, gap: 12, ladder: 660 };
  text(`HOW FAR DOES ${shortName(c.name).toUpperCase()} CLIMB?`, story ? 38 : 28, W / 2, L.head, { max: W - 120, font: '"IBM Plex Mono"', weight: 700, color: '#D9A441', spacing: 4 });
  const [hx, hy, hs, hr] = L.hero;
  tile(hero, c, hx, hy, hs, hr, '#D9A441');
  text(shortName(c.name), story ? 60 : 50, W / 2, L.name, { font: 'Fraunces', weight: 700 });
  text(`${c.series}${c.form ? ` · ${c.form}` : ''}`, story ? 28 : 24, W / 2, L.sub, { color: '#A69C8C' });
  text(r.climbed === r.total ? `Cleared all ${r.total}!` : `Climbed ${r.climbed} of ${r.total}`, story ? 76 : 64, W / 2, L.result,
    { font: 'Fraunces', weight: 700, color: '#D9A441' });

  // The ladder: rung numbers under each picture, rows of `cols`.
  r.fights.forEach((f, i) => {
    const row = Math.floor(i / L.cols);
    const inRow = Math.min(L.cols, r.fights.length - row * L.cols);
    const rowW = inRow * L.thumb + (inRow - 1) * L.gap;
    const left = (W - rowW) / 2 + (i % L.cols) * (L.thumb + L.gap);
    const top = L.ladder + row * (L.thumb + (story ? 70 : 60));
    const state = !f.reached ? 'unreached' : f.outcome === 'win' ? 'beaten' : 'lost';
    x.globalAlpha = state === 'unreached' ? 0.3 : 1;
    tile(foes[i], f.opponent, left, top, L.thumb, L.thumb / 5, state === 'beaten' ? '#8FBF6B' : state === 'lost' ? '#E15252' : null);
    x.globalAlpha = 1;
    text(String(f.rung), story ? 24 : 20, left + L.thumb / 2, top + L.thumb + (story ? 36 : 30),
      { font: '"IBM Plex Mono"', color: state === 'lost' ? '#E15252' : '#7A7264' });
  });

  const fy = H - (story ? 120 : 100);
  text('How far would yours climb?', story ? 34 : 28, W / 2, fy - (story ? 90 : 50), { font: 'Fraunces', weight: 700, color: '#D8D0C0' });

  // The logo, then the address, centered together.
  x.font = '600 30px "IBM Plex Mono", monospace';
  const site = 'powerscale.online';
  const start = (W - (44 + 16 + x.measureText(site).width)) / 2;
  if (logo) x.drawImage(logo, start, fy, 44, 44);
  x.fillStyle = '#A69C8C';
  x.textAlign = 'left';
  x.fillText(site, start + 60, fy + 32);

  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  const slug = shortName(c.name).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'character';
  const file = new File([blob], `${slug}-gauntlet${story ? '-story' : ''}.png`, { type: 'image/png' });
  if (matchMedia('(pointer: coarse)').matches && navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: `How far does ${shortName(c.name)} climb?` }); return; } catch { /* dismissed: download */ }
  }
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = file.name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 5000);
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
