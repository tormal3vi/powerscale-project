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

// The mockup's 6-color accent palette, assigned deterministically per
// character id so the same character always gets the same color -
// same idea as the mockup's per-character accent picker, just derived
// instead of manually chosen per card.
const ACCENT_VARS = ['--accent-0', '--accent-1', '--accent-2', '--accent-3', '--accent-4', '--accent-5'];

function accentFor(id) {
  const varName = ACCENT_VARS[id % ACCENT_VARS.length];
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

function initialFor(name) {
  const trimmed = (name || '?').trim();
  return trimmed.charAt(0).toUpperCase() || '?';
}
