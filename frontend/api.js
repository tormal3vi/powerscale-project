// Thin fetch wrappers over the backend's /api/* routes (backend/main.py).
// No business logic here - just JSON in, JSON out, same-origin (the
// backend mounts this whole frontend/ directory as static files, so
// there's no CORS to configure).

const API_BASE = '';

async function apiGet(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw Object.assign(new Error(body.detail || `${res.status} ${res.statusText}`), { status: res.status });
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

async function apiSend(method, path, payload) {
  const res = await fetch(API_BASE + path, {
    method,
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
  boardVersion: () => apiGet('/api/board/version'),
  getThread: (id) => apiGet(`/api/posts/${id}`),
  createPost: (post) => apiPost('/api/posts', post),
  deletePost: (id) => apiDelete(`/api/posts/${id}`),
  likePost: (id) => apiPost(`/api/posts/${id}/like`, {}),
  // XHR rather than fetch: only XHR reports upload progress (onProgress
  // gets 0-100).
  uploadAvatar: (blob, onProgress) => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', '/api/me/avatar');
    xhr.setRequestHeader('Content-Type', blob.type || 'image/png');
    xhr.responseType = 'json';
    if (onProgress) {
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response);
      else reject(new Error((xhr.response && xhr.response.detail) || `${xhr.status} ${xhr.statusText}`));
    };
    xhr.onerror = () => reject(new Error('Upload failed - check your connection'));
    xhr.send(blob);
  }),
  removeAvatar: () => apiDelete('/api/me/avatar'),
  myProfile: () => apiGet('/api/me/profile'),
  userProfile: (username) => apiGet(`/api/users/${encodeURIComponent(username)}`),
  saveProfile: (profile) => apiSend('PUT', '/api/me/profile', profile),
  changePassword: (current, next) => apiPost('/api/me/password', { current_password: current, new_password: next }),
  logoutOtherDevices: () => apiPost('/api/me/logout-others', {}),
  deleteAccount: (password) => apiPost('/api/me/delete', { password }),
  replaceCharacterImage: async (id, blob) => {
    const res = await fetch(`/api/characters/${id}/image`, { method: 'PUT', headers: { 'Content-Type': blob.type || 'image/png' }, body: blob });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `${res.status} ${res.statusText}`);
    }
    return res.json();
  },
  resetCharacterImage: (id) => apiDelete(`/api/characters/${id}/image`),
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
  // Split at ';' or ', ' only: "170,000 Year Cicada Nymph" has a comma
  // inside a number and used to become just "170".
  return (name || '').split(/;|,\s/)[0].trim();
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

// --- character search (board matchup picker, favorite character) -------------

let rosterPromise = null;
function roster() {
  if (!rosterPromise) {
    rosterPromise = Api.listCharacters().then((r) => r.characters.map((c) => ({
      ...c, _name: fold(c.name), _aliases: fold(c.aliases), _cat: fold(c.category),
    })));
  }
  return rosterPromise;
}

// Name prefix first, then anywhere in the name, then aliases, then series.
function searchRoster(all, q, excludeId, limit = 8) {
  const hits = [];
  for (const c of all) {
    if (c.id === excludeId) continue;
    const rank = c._name.startsWith(q) ? 0 : c._name.includes(q) ? 1
      : c._aliases.includes(q) ? 2 : c._cat.includes(q) ? 3 : -1;
    if (rank >= 0) hits.push([rank, c]);
  }
  hits.sort((x, y) => x[0] - y[0] || x[1].name.localeCompare(y[1].name));
  return hits.slice(0, limit).map((h) => h[1]);
}

// --- character pictures ------------------------------------------------------

// Wiki pictures arrive size-free; ask Fandom's CDN for a square of the
// size shown. "top-crop" keeps the head of a full-body render in frame
// ("smart" crop cut some off at the waist). Admin replacements are
// already small squares from our own API and are used as-is.
// Every <img> showing one MUST carry referrerpolicy="no-referrer": the CDN
// answers 404 to thumbnails requested with another site's Referer (hotlink
// protection; verified live). Don't swap that for a page-wide no-referrer
// <meta> - browsers then send "Origin: null" on our own POST/PUT/DELETEs,
// which same_origin() in community_api.py rightly rejects.
function characterPictureUrl(url, px) {
  if (!url) return null;
  if (!url.startsWith('https://static.wikia.nocookie.net/')) return url;
  const [path, query] = url.split('?');
  return `${path}/top-crop/width/${px}/height/${px}${query ? `?${query}` : ''}`;
}

// Inside of a character's square tile: the colored initial, covered by
// the picture when there is one. If the picture fails to load, it
// removes itself and the initial shows again.
function characterTileInner(name, url, px) {
  const pic = characterPictureUrl(url, px);
  return `<span class="tile-initial">${escapeHtml(initialFor(name))}</span>` + (pic
    ? `<img src="${escapeHtml(pic)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.classList.remove('has-pic');this.remove()">`
    : '');
}

// Fill an existing tile element. `px` is the image size to fetch - about
// twice the displayed size, for sharp high-density screens.
function setCharacterTile(el, name, url, px) {
  if (!el) return;
  el.classList.toggle('has-pic', !!url);
  el.innerHTML = characterTileInner(name, url, px);
}

// Shrink a picked photo before uploading it (phone photos are often
// 5-10 MB): at most maxSide px, EXIF rotation applied. If the browser
// can't decode it (e.g. HEIC outside Safari), the original goes up and
// the server explains what's wrong with it.
async function shrinkImage(file, maxSide = 768) {
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bmp.width * scale));
    canvas.height = Math.max(1, Math.round(bmp.height * scale));
    canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/webp', 0.9));
    return blob || file;
  } catch {
    return file;
  }
}

// --- user avatars (board, topbar, profile) ------------------------------------

const USER_COLORS = ['#E15252', '#D9A441', '#8FBF6B', '#C777D6', '#4C8DE0', '#4CC2B0'];

function userColor(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return USER_COLORS[h % USER_COLORS.length];
}

// The user's picture if they've uploaded one, else their colored initial.
// `cls` sizes it (post-avatar, reply-avatar, topbar-avatar, settings-avatar, pic-tile, user-pop-avatar).
// avatarUrl only ever comes from our own API (/api/avatars/<name>?v=<n>).
// The gold "ADMIN" pill. `solid` is the filled version used on profiles
// (settings header, popover, phone menu); the outline one sits in feeds.
function adminBadgeHtml({ solid = false } = {}) {
  const shield = `<svg width="10" height="10" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.2 14 3.8v3.6c0 3.7-2.5 6.1-6 7.4-3.5-1.3-6-3.7-6-7.4V3.8L8 1.2Z" ${solid ? 'fill="currentColor"' : 'stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"'}/></svg>`;
  return `<span class="admin-badge${solid ? ' solid' : ''}" title="Site admin">${shield}Admin</span>`;
}

// "Sep 2026"
function monthYear(iso) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
}

function userAvatarHtml(name, avatarUrl, cls, { admin = false } = {}) {
  const classes = `${cls}${admin ? ' is-admin' : ''}${avatarUrl ? ' has-img' : ''}`;
  if (avatarUrl) return `<span class="${classes}"><img src="${escapeHtml(avatarUrl)}" alt="" loading="lazy"></span>`;
  return `<span class="${classes}" style="background:${userColor(name)}">${escapeHtml(initialFor(name))}</span>`;
}
