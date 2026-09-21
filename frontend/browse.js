// Browse screen: fetches the full character list once (small payload,
// see backend/main.py's /api/characters - no pagination needed at this
// size) and does all search/category filtering client-side, so typing
// in the search box is instant rather than a round-trip per keystroke.

const state = {
  characters: [],
  categories: [],
  activeCategory: 'All',
  query: '',
  selected: [], // up to 2 {id, name}
  showAllDefault: false, // "show the full roster" override for the capped default view
};

const grid = document.getElementById('character-grid');
const emptyState = document.getElementById('empty-state');
const filterRow = document.getElementById('filter-row');
const compareBar = document.getElementById('compare-bar');
const topbarMeta = document.getElementById('topbar-meta');
const searchInput = document.getElementById('search-input');
const gridNote = document.getElementById('grid-note');

// With no search text and no category picked, showing the full roster
// (1000+ names, alphabetical) is an overwhelming wall of mostly-obscure
// characters rather than a clean grid - so that specific default state
// shows a capped, tidy set instead (matching the mockup's un-scrolled
// 12-card layout) with an explicit note that it's a subset. The instant
// a search or category filter is applied, the cap lifts and every
// matching result shows - this only affects the very first, filter-less
// screen.
const DEFAULT_VIEW_LIMIT = 12;

function showToast(message) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2600);
}

function isSelected(id) {
  return state.selected.some((c) => c.id === id);
}

function toggleSelect(character) {
  if (isSelected(character.id)) {
    state.selected = state.selected.filter((c) => c.id !== character.id);
  } else {
    if (state.selected.length >= 2) {
      showToast('You can only compare 2 characters at a time — remove one first.');
      return;
    }
    state.selected.push({ id: character.id, name: character.name });
  }
  renderGrid();
  renderCompareBar();
}

function renderFilterRow() {
  const names = ['All', ...state.categories.map((c) => c.name)];
  filterRow.innerHTML = '';
  for (const name of names) {
    const btn = document.createElement('button');
    btn.className = 'filter-pill' + (name === state.activeCategory ? ' active' : '');
    btn.textContent = name;
    btn.addEventListener('click', () => {
      state.activeCategory = name;
      renderFilterRow();
      renderGrid();
    });
    filterRow.appendChild(btn);
  }
}

function filteredCharacters() {
  const q = state.query.trim().toLowerCase();
  return state.characters.filter((c) => {
    if (state.activeCategory !== 'All' && c.category !== state.activeCategory) return false;
    if (q && !c.name.toLowerCase().includes(q)) return false;
    return true;
  });
}

function characterCard(c) {
  const card = document.createElement('div');
  card.className = 'character-card';

  const head = document.createElement('div');
  head.className = 'character-card-head';

  const avatar = document.createElement('div');
  avatar.className = 'avatar avatar-md';
  avatar.style.background = accentFor(c.id);
  avatar.textContent = initialFor(c.name);
  head.appendChild(avatar);

  const nameBlock = document.createElement('div');
  nameBlock.style.minWidth = '0';
  const nameEl = document.createElement('div');
  nameEl.className = 'character-card-name';
  nameEl.textContent = c.name;
  nameEl.title = c.name;
  const catEl = document.createElement('div');
  catEl.className = 'character-card-category';
  catEl.textContent = c.category;
  nameBlock.appendChild(nameEl);
  nameBlock.appendChild(catEl);
  head.appendChild(nameBlock);
  card.appendChild(head);

  const statsRow = document.createElement('div');
  statsRow.className = 'character-card-stats';
  const tierEl = document.createElement('div');
  tierEl.className = 'tier-badge';
  tierEl.innerHTML = 'Tier <strong>' + (c.tier_label || '—') + '</strong>';
  statsRow.appendChild(tierEl);
  if (c.is_multi_form) {
    const formBadge = document.createElement('div');
    formBadge.className = 'form-count-badge';
    formBadge.textContent = c.form_count + ' forms';
    statsRow.appendChild(formBadge);
  }
  card.appendChild(statsRow);

  const btn = document.createElement('button');
  const selected = isSelected(c.id);
  btn.className = 'card-action-button' + (selected ? ' selected' : '');
  btn.textContent = selected ? '✓ Added' : '+ Add to comparison';
  btn.addEventListener('click', () => toggleSelect(c));
  card.appendChild(btn);

  return card;
}

function isDefaultView() {
  return !state.query.trim() && state.activeCategory === 'All';
}

function renderGrid() {
  const matches = filteredCharacters();
  const capped = isDefaultView() && !state.showAllDefault && matches.length > DEFAULT_VIEW_LIMIT;
  const list = capped ? matches.slice(0, DEFAULT_VIEW_LIMIT) : matches;

  grid.innerHTML = '';
  emptyState.style.display = matches.length ? 'none' : 'block';

  if (capped) {
    gridNote.innerHTML = '';
    gridNote.append(`Showing ${DEFAULT_VIEW_LIMIT} of ${matches.length.toLocaleString()} — search or pick a category to see the rest, or `);
    const showAllBtn = document.createElement('button');
    showAllBtn.textContent = 'show the full roster';
    showAllBtn.addEventListener('click', () => {
      state.showAllDefault = true;
      renderGrid();
    });
    gridNote.appendChild(showAllBtn);
  } else {
    gridNote.textContent = '';
  }

  const frag = document.createDocumentFragment();
  for (const c of list) frag.appendChild(characterCard(c));
  grid.appendChild(frag);
}

function renderCompareBar() {
  if (state.selected.length === 0) {
    compareBar.style.display = 'none';
    return;
  }
  compareBar.style.display = 'flex';
  const names = state.selected.map((c) => c.name).join(' vs. ');
  compareBar.innerHTML = '';

  const label = document.createElement('div');
  label.className = 'compare-bar-label';
  label.innerHTML = state.selected.length === 2
    ? 'Comparing <strong>' + escapeHtml(names) + '</strong>'
    : 'Selected <strong>' + escapeHtml(names) + '</strong> — pick one more';
  compareBar.appendChild(label);

  const cta = document.createElement('button');
  cta.className = 'compare-bar-cta';
  cta.textContent = 'Compare →';
  cta.disabled = state.selected.length !== 2;
  cta.addEventListener('click', () => {
    if (state.selected.length !== 2) return;
    const [a, b] = state.selected;
    window.location.href = `compare.html?a=${a.id}&b=${b.id}`;
  });
  compareBar.appendChild(cta);
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

async function init() {
  try {
    const [categoriesRes, charactersRes] = await Promise.all([
      Api.listCategories(),
      Api.listCharacters(),
    ]);
    state.categories = categoriesRes;
    state.characters = charactersRes.characters;
    topbarMeta.textContent = `${charactersRes.total.toLocaleString()} characters · ${categoriesRes.length} categories`;
    renderFilterRow();
    renderGrid();
  } catch (err) {
    topbarMeta.textContent = 'Failed to load';
    emptyState.textContent = 'Could not reach the API: ' + err.message;
    emptyState.style.display = 'block';
  }
}

searchInput.addEventListener('input', (e) => {
  state.query = e.target.value;
  renderGrid();
});

// --- Add a character (live fetch from the wiki) -----------------------

const addPanel = document.getElementById('add-character-panel');
const addToggle = document.getElementById('add-character-toggle');
const addForm = document.getElementById('add-character-form');
const addInput = document.getElementById('add-character-input');
const addSubmit = document.getElementById('add-character-submit');
const addStatus = document.getElementById('add-character-status');

addToggle.addEventListener('click', () => {
  const isOpen = addPanel.style.display !== 'none';
  addPanel.style.display = isOpen ? 'none' : 'block';
  if (!isOpen) addInput.focus();
});

addForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const query = addInput.value.trim();
  if (!query) {
    addStatus.textContent = 'Enter a character name or URL first.';
    addStatus.className = 'add-character-status error';
    return;
  }

  addSubmit.disabled = true;
  addStatus.textContent = `Fetching ${query}…`;
  addStatus.className = 'add-character-status';

  try {
    const added = await Api.fetchCharacter(query);
    // Re-fetch both lists rather than just splicing the one row in - a
    // brand-new category shows up as a new filter pill too, same as a
    // fresh page load would show.
    const [categoriesRes, charactersRes] = await Promise.all([Api.listCategories(), Api.listCharacters()]);
    state.categories = categoriesRes;
    state.characters = charactersRes.characters;
    topbarMeta.textContent = `${charactersRes.total.toLocaleString()} characters · ${categoriesRes.length} categories`;
    renderFilterRow();
    renderGrid();

    addStatus.textContent = `Added "${added.name}" (${added.category}).`;
    addStatus.className = 'add-character-status success';
    addInput.value = '';
  } catch (err) {
    addStatus.textContent = `Couldn't fetch "${query}": ${err.message}`;
    addStatus.className = 'add-character-status error';
  } finally {
    addSubmit.disabled = false;
  }
});

init();
