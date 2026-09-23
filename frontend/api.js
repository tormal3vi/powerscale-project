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

async function apiDelete(path) {
  const res = await fetch(API_BASE + path, { method: 'DELETE' });
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

  me: () => apiGet('/api/auth/me'),
  register: (username, password) => apiPost('/api/auth/register', { username, password }),
  login: (username, password) => apiPost('/api/auth/login', { username, password }),
  logout: () => apiPost('/api/auth/logout', {}),

  setOverride: (charA, charB, formA, formB, winnerId, note) =>
    apiPost('/api/overrides', { char_a: charA, char_b: charB, form_a: formA, form_b: formB, winner_id: winnerId, note }),
  removeOverride: (charA, charB, formA, formB) =>
    apiDelete(`/api/overrides?${new URLSearchParams({ a: charA, b: charB, fa: formA, fb: formB })}`),

  listPosts: (before) => apiGet('/api/posts' + (before ? `?before=${before}` : '')),
  getThread: (id) => apiGet(`/api/posts/${id}`),
  createPost: (post) => apiPost('/api/posts', post),
  deletePost: (id) => apiDelete(`/api/posts/${id}`),
  likePost: (id) => apiPost(`/api/posts/${id}/like`, {}),
  uploadAvatar: async (blob) => {
    const res = await fetch('/api/me/avatar', { method: 'PUT', headers: { 'Content-Type': blob.type || 'image/png' }, body: blob });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `${res.status} ${res.statusText}`);
    }
    return res.json();
  },
  removeAvatar: () => apiDelete('/api/me/avatar'),
  matchupPreview: (a, b, fa, fb) =>
    apiGet(`/api/matchups/preview?${new URLSearchParams({ a, b, ...(fa ? { fa } : {}), ...(fb ? { fb } : {}) })}`),
};

// The logged-in user (or null), fetched once per page load and shared.
let _mePromise = null;
function currentUser() {
  if (!_mePromise) _mePromise = Api.me().then((r) => r.user).catch(() => null);
  return _mePromise;
}

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

// Accent-insensitive, so "Onoki" finds "Ōnoki" and "kugo" finds "Kūgo".
function fold(s) {
  return (s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

function shortName(name) {
  // Names often carry a whole alias list ("Rudeus Greyrat (...); Rudi;
  // Rudeus the Quagmire; ...") - the first alias is enough in a sentence.
  return (name || '').split(/[;,]/)[0].trim();
}

// "Dante (Devil May Cry)" -> "Dante": for tight spots like "Dante wins".
function bareName(name) {
  return shortName(name).replace(/\s*\([^)]*\)/g, '').trim() || shortName(name);
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

// "up to possibly Galaxy level" - or '' when the range has no higher peak.
// Badges/rows show the baseline (lower) value of a range - deliberate,
// see calculator.py's select_form() docstring - so the peak is shown
// alongside whenever it differs (a hover tooltip alone doesn't help on touch).
function peakHintText(range, rawText) {
  if (!range || !range.peak_label || range.peak_label === range.baseline_label) return '';
  const suffix = peakConditionSuffix(rawText, range.peak_label);
  // The qualifier can itself be "up to" ("7-A, up to 5-C with Fighting
  // Spirit") - don't say it twice.
  const q = (range.peak_qualifier || '').replace(/^up to\b\s*/i, '');
  const qualifier = q ? q + ' ' : '';
  return 'up to ' + (suffix ? qualifier + suffix : qualifier + (prettifyLabel(range.peak_label) || ''));
}

function peakHintHtml(range, rawText) {
  const text = peakHintText(range, rawText);
  return text ? `<div class="stat-peak-hint">${escapeHtml(text)}</div>` : '';
}

// A normalized label as the wiki itself wrote it ("Wall level", not the
// lower-cased ladder key) when it appears in the raw text; tier codes and
// anything not found fall back to prettifyLabel.
function labelAsWritten(label, rawText) {
  if (!label) return null;
  if (/\d/.test(label) || !rawText) return prettifyLabel(label);
  const idx = rawText.toLowerCase().indexOf(label.toLowerCase());
  return idx === -1 ? prettifyLabel(label) : rawText.slice(idx, idx + label.length);
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

// What to show in place of a stat value the calculator couldn't score.
// Most often the wiki itself says "Unknown" (e.g. Dhruv Lakdawalla's
// Speed/Durability) - say so, rather than implying the site failed to read it.
function missingStatText(raw) {
  if (!raw || !raw.trim()) return 'Not listed';
  return /^\s*(?:(?:at least|at most|likely|possibly)\s+)?unknown\b/i.test(raw) ? 'Unknown' : 'Unscored';
}

function initialFor(name) {
  const trimmed = (name || '?').trim();
  return trimmed.charAt(0).toUpperCase() || '?';
}

// --- user avatars (board, topbar, profile) ------------------------------------

const USER_COLORS = ['#E15252', '#D9A441', '#8FBF6B', '#C777D6', '#4C8DE0', '#4CC2B0'];

function userColor(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return USER_COLORS[h % USER_COLORS.length];
}

// The user's picture if they've uploaded one, else their colored initial.
// `cls` sizes it (post-avatar, reply-avatar, topbar-avatar, profile-avatar).
// avatarUrl only ever comes from our own API (/api/avatars/<name>?v=<n>).
function userAvatarHtml(name, avatarUrl, cls, { admin = false } = {}) {
  const classes = `${cls}${admin ? ' is-admin' : ''}${avatarUrl ? ' has-img' : ''}`;
  if (avatarUrl) return `<span class="${classes}"><img src="${escapeHtml(avatarUrl)}" alt="" loading="lazy"></span>`;
  return `<span class="${classes}" style="background:${userColor(name)}">${escapeHtml(initialFor(name))}</span>`;
}
