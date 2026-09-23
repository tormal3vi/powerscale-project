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

function initialFor(name) {
  const trimmed = (name || '?').trim();
  return trimmed.charAt(0).toUpperCase() || '?';
}
