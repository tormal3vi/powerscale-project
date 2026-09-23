// Thin fetch wrappers over the backend's /api/* routes (backend/main.py).
// No business logic here - just JSON in, JSON out, same-origin (the
// backend mounts this whole frontend/ directory as static files, so
// there's no CORS to configure).

const API_BASE = '';

async function apiGet(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function apiPost(path, payload) {
  const res = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

const Api = {
  listCharacters: () => apiGet('/api/characters'),
  getCharacter: (id) => apiGet(`/api/characters/${id}`),
  listCategories: () => apiGet('/api/categories'),
  fetchCharacter: (query) => apiPost('/api/characters/fetch', { query }),
  compare: (charA, charB, formA, formB) =>
    apiPost('/api/compare', { char_a: charA, char_b: charB, form_a: formA || null, form_b: formB || null }),
};

// One distinct color per character id, generated rather than picked from
// a small fixed palette - a 6-color cycle meant any two characters
// exactly 6 ids apart (common in a matchup, since ids are assigned in
// scrape order within a category) landed on the identical accent, which
// is exactly the ambiguity color-coding exists to prevent. The golden-
// angle hue step (~137.5 degrees) spreads consecutive ids across the
// full hue wheel with no visible clustering, so distinct ids reliably
// look distinct; a given id always maps to the same hue, so a
// character's color still stays stable across screens/sessions.
function accentHue(id) {
  return (id * 137.508) % 360;
}

function accentFor(id) {
  return `hsl(${accentHue(id).toFixed(1)}, 65%, 58%)`;
}

// Colors for the two sides of one matchup. Distinct ids can still land
// on nearly the same hue (Kratos 2623 and Dante 2678 are ~3 degrees
// apart - both pink), so when the pair is too close, side B takes the
// opposite hue. Each character keeps its own color everywhere else.
function accentPair(idA, idB) {
  const hueA = accentHue(idA);
  let hueB = accentHue(idB);
  const gap = Math.min(Math.abs(hueA - hueB), 360 - Math.abs(hueA - hueB));
  if (gap < 45) hueB = (hueA + 180) % 360;
  return [accentFor(idA), `hsl(${hueB.toFixed(1)}, 65%, 58%)`];
}

// --- shared display helpers (compare, character, tournament, board) ---------

const HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

// Safe for both text and attribute values - quotes included, since wiki
// text (names like `A, "Third Raikage"`) and user posts end up inside
// title="..." / href="..." attributes too.
function escapeHtml(s) {
  return (s == null ? '' : String(s)).replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);
}

function shortName(name) {
  // Names often carry a whole alias list ("Rudeus Greyrat (...); Rudi;
  // Rudeus the Quagmire; ...") - the first alias is enough in a sentence.
  return (name || '').split(/[;,]/)[0].trim();
}

function prettifyLabel(label) {
  if (!label) return null;
  // Tier codes ("7-b", "low 2-c", "high 1-a") read best fully upper-cased;
  // everything else (descriptive names like "massively hypersonic+") reads
  // best title-cased. A code always contains a digit, so that's the split.
  if (/\d/.test(label)) return label.toUpperCase();
  const fixups = { Ftl: 'FTL' };
  // Split on spaces AND hyphens (keeping them) so "multi-solar system
  // level" becomes "Multi-Solar System Level", not "Multi-solar ...".
  return label
    .split(/([ -])/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .map((w) => fixups[w] || w)
    .join('');
}

function peakConditionSuffix(rawText, peakLabel) {
  // The clean peak_label alone ("Island Level") drops WHY it's higher
  // - normalizer only tracks probabilistic qualifiers (possibly, at
  // least, ...) as structured data, not domain conditions like "with
  // magic"/"physically". Pull that phrase straight from the raw wiki
  // text instead: from the peak label up to the next comma/pipe/open-
  // paren, e.g. "Island level with magic" out of "Street level
  // physically, Island level with magic (...)".
  if (!rawText || !peakLabel) return null;
  const idx = rawText.toLowerCase().indexOf(peakLabel.toLowerCase());
  if (idx === -1) return null;
  let end = rawText.length;
  for (const stop of [',', '|', '(']) {
    const stopIdx = rawText.indexOf(stop, idx);
    if (stopIdx !== -1 && stopIdx < end) end = stopIdx;
  }
  const suffix = rawText.slice(idx, end).trim();
  return suffix || null;
}

function peakHintHtml(range, rawText) {
  // Badges/rows show the baseline (lower) value of a range - deliberate,
  // see calculator.py's select_form() docstring - so show the peak inline
  // whenever it differs (a hover tooltip alone doesn't help on touch).
  if (!range || !range.peak_label || range.peak_label === range.baseline_label) return '';
  const suffix = peakConditionSuffix(rawText, range.peak_label);
  const qualifier = range.peak_qualifier ? range.peak_qualifier + ' ' : '';
  const text = suffix ? qualifier + suffix : qualifier + (prettifyLabel(range.peak_label) || '');
  return `<div class="stat-peak-hint">up to ${escapeHtml(text)}</div>`;
}

function defaultFormIndex(forms) {
  // Mirrors calculator.select_form()'s own default exactly: highest
  // tier.baseline, unscored forms sort last, first tie wins.
  let bestIdx = 0;
  let bestVal = -Infinity;
  forms.forEach((f, i) => {
    const v = f.tier.baseline;
    const val = v === null || v === undefined ? -Infinity : v;
    if (val > bestVal) { bestVal = val; bestIdx = i; }
  });
  return bestIdx;
}

function initialFor(name) {
  const trimmed = (name || '?').trim();
  return trimmed.charAt(0).toUpperCase() || '?';
}
