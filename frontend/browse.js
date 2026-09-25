// Browse screen: fetches the full character list once (small payload,
// see backend/main.py's /api/characters - no pagination needed at this
// size) and does all search/category/tier filtering and sorting
// client-side, so typing in the search box is instant rather than a
// round-trip per keystroke.

const state = {
  characters: [],
  categories: [],
  activeCategory: 'All',
  activeSubseries: null, // one part of a split series (DC: 'Arkham'...), or null for all of it
  query: '',
  sort: 'alpha', // 'alpha' | 'strong' | 'weak'
  tierGroup: 'any', // 'any' | '1'..'10'
  selected: [], // up to 2 {id, name}
  showAllDefault: false, // "show the full roster" override for the capped default view
};

const addToggle = pillButton('+ Add character');
renderTopbar([addToggle]);
const pageSubtitle = document.getElementById('page-subtitle');
const DEFAULT_SUBTITLE = pageSubtitle.textContent;

const grid = document.getElementById('character-grid');
const emptyState = document.getElementById('empty-state');
const filterRow = document.getElementById('filter-row');
const subfilterRow = document.getElementById('subfilter-row');
const compareBar = document.getElementById('compare-bar');
const searchInput = document.getElementById('search-input');
// The full placeholder is cut off mid-word on a phone.
if (window.matchMedia('(max-width: 640px)').matches) searchInput.placeholder = 'Search characters…';
const gridNote = document.getElementById('grid-note');
const sortSelect = document.getElementById('sort-select');
const tierSelect = document.getElementById('tier-select');

// With no search, category, tier filter or sort picked, showing the full
// roster (1000+ names, alphabetical) is an overwhelming wall of mostly-
// obscure characters rather than a clean grid - so that specific default
// state shows a capped, tidy set instead (matching the mockup's un-
// scrolled 12-card layout) with an explicit note that it's a subset.
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

// "HIGH 6-A" / "LOW 1-C" / "10-B" -> 6 / 1 / 10.
function tierNumber(label) {
  const m = /(\d+)-[ABC]/i.exec(label || '');
  return m ? m[1] : null;
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
    state.selected.push({ id: character.id, name: character.name, image_url: character.image_url });
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
      state.activeSubseries = null;
      renderFilterRow();
      renderGrid();
    });
    filterRow.appendChild(btn);
  }
  keepActiveInView(filterRow);
  renderSubfilterRow();
}

// On phones each row is one line you swipe; rebuilding it on a tap reset
// the swipe to the start, hiding the pill just picked (DC, far right).
function keepActiveInView(row) {
  const active = row.querySelector('.filter-pill.active');
  if (active && row.scrollWidth > row.clientWidth) {
    row.scrollLeft = active.offsetLeft - row.offsetLeft - (row.clientWidth - active.offsetWidth) / 2;
  }
}

// A second row under a series split into parts (DC: Comics, Arkham,
// DCEU...), shown only while that series is picked.
function renderSubfilterRow() {
  const cat = state.categories.find((c) => c.name === state.activeCategory);
  const parts = cat ? cat.subseries || [] : [];
  subfilterRow.hidden = parts.length < 2;
  filterRow.classList.toggle('has-sub', !subfilterRow.hidden);
  subfilterRow.innerHTML = '';
  if (subfilterRow.hidden) return;
  for (const part of [{ name: null, count: cat.count }, ...parts]) {
    const btn = document.createElement('button');
    btn.className = 'filter-pill sub' + (part.name === state.activeSubseries ? ' active' : '');
    btn.innerHTML = `${escapeHtml(part.name || `All ${cat.name}`)} <span class="filter-count">${part.count.toLocaleString()}</span>`;
    btn.addEventListener('click', () => {
      state.activeSubseries = part.name;
      renderSubfilterRow();
      renderGrid();
    });
    subfilterRow.appendChild(btn);
  }
  keepActiveInView(subfilterRow);
}

function filteredCharacters() {
  const q = fold(state.query.trim());
  const list = state.characters.filter((c) => {
    if (state.activeCategory !== 'All' && c.category !== state.activeCategory) return false;
    if (state.activeSubseries && c.subseries !== state.activeSubseries) return false;
    if (state.tierGroup !== 'any' && tierNumber(c.tier_label) !== state.tierGroup) return false;
    // Search the display name AND the full alias list ("Kakarot",
    // "Salamander", "Homulily" - no longer part of the shown name).
    if (q && !fold(c.name).includes(q) && !fold(c.aliases).includes(q)) return false;
    return true;
  });
  if (state.sort !== 'alpha') {
    const dir = state.sort === 'strong' ? -1 : 1;
    // Unscored tiers always sort last, whichever direction.
    list.sort((a, b) => {
      if (a.tier_score == null) return b.tier_score == null ? 0 : 1;
      if (b.tier_score == null) return -1;
      return dir * (a.tier_score - b.tier_score);
    });
  }
  return list;
}

// "HIGH 3-A" -> "High 3-A"
function prettifyTier(label) {
  return label.toLowerCase().replace(/^(low|high)\b/, (w) => w[0].toUpperCase() + w.slice(1)).replace(/\d-[abc]/, (t) => t.toUpperCase());
}

function characterCard(c, rank) {
  const card = document.createElement('div');
  card.className = 'character-card';

  const head = document.createElement('div');
  head.className = 'character-card-head';

  const avatar = document.createElement('div');
  avatar.className = 'avatar avatar-md';
  avatar.style.background = accentFor(c.id);
  setCharacterTile(avatar, c.name, c.image_url, 96);
  head.appendChild(avatar);

  const nameBlock = document.createElement('div');
  nameBlock.style.minWidth = '0';
  const nameEl = document.createElement('a');
  nameEl.className = 'character-card-name';
  nameEl.href = `character.html?id=${c.id}`;
  nameEl.textContent = c.name;
  nameEl.title = c.name;
  const catEl = document.createElement('div');
  catEl.className = 'character-card-category';
  catEl.textContent = seriesLabel(c);
  nameBlock.appendChild(nameEl);
  nameBlock.appendChild(catEl);
  head.appendChild(nameBlock);
  if (rank) {
    const rankEl = document.createElement('div');
    rankEl.className = 'rank-badge' + (rank === 1 ? ' first' : '');
    rankEl.textContent = `#${rank}`;
    card.appendChild(rankEl);
    card.classList.add('ranked');
    if (rank === 1) card.classList.add('rank-first');
  }
  // Phones show the tier in the card's top-right corner instead of a row
  // under the name (phone design); CSS picks one.
  const corner = document.createElement('div');
  corner.className = 'card-tier-corner';
  corner.innerHTML = 'Tier<span></span>';
  corner.querySelector('span').textContent = c.tier_label ? prettifyTier(c.tier_label) : '—';
  head.appendChild(corner);
  card.appendChild(head);

  const statsRow = document.createElement('div');
  statsRow.className = 'character-card-stats';
  const tierEl = document.createElement('div');
  tierEl.className = 'tier-badge';
  tierEl.innerHTML = 'Tier <strong></strong>';
  tierEl.querySelector('strong').textContent = c.tier_label || '—';
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
  return !state.query.trim() && state.activeCategory === 'All' && state.tierGroup === 'any' && state.sort === 'alpha';
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
  } else if (state.sort !== 'alpha') {
    gridNote.textContent = `${matches.length.toLocaleString()} characters, ranked by their default form's Tier.`;
  } else {
    gridNote.textContent = '';
  }
  const where = state.activeCategory === 'All' ? 'across every category'
    : `in ${state.activeCategory}${state.activeSubseries ? ` · ${state.activeSubseries}` : ''}`;
  pageSubtitle.textContent = state.sort === 'strong' ? `Sorted by strength, ${where}.`
    : state.sort === 'weak' ? `Sorted weakest first, ${where}.` : DEFAULT_SUBTITLE;

  const ranked = state.sort !== 'alpha';
  const frag = document.createDocumentFragment();
  list.forEach((c, i) => frag.appendChild(characterCard(c, ranked && c.tier_score != null ? i + 1 : null)));
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

  // Phones show the picked characters as chips (picture + short name).
  const chips = document.createElement('div');
  chips.className = 'compare-bar-chips';
  chips.innerHTML = state.selected.map((c) => `
    <span class="compare-chip"><span class="compare-chip-tile${c.image_url ? ' has-pic' : ''}" style="background:${accentFor(c.id)}">${
      characterTileInner(c.name, c.image_url, 44)}</span><span class="compare-chip-name">${escapeHtml(bareName(c.name))}</span></span>`).join('');
  compareBar.appendChild(chips);

  const label = document.createElement('div');
  label.className = 'compare-bar-label';
  label.innerHTML = state.selected.length === 2 ? 'Comparing <strong></strong>' : 'Selected <strong></strong> — pick one more';
  label.querySelector('strong').textContent = names;
  compareBar.appendChild(label);

  const cta = document.createElement('button');
  cta.className = 'compare-bar-cta';
  cta.innerHTML = '<span class="desktop-only">Compare →</span><span class="phone-only">Compare</span>';
  cta.disabled = state.selected.length !== 2;
  cta.addEventListener('click', () => {
    if (state.selected.length !== 2) return;
    const [a, b] = state.selected;
    window.location.href = `compare.html?a=${a.id}&b=${b.id}`;
  });
  compareBar.appendChild(cta);
}

// --- random / daily matchups -----------------------------------------------

function goToMatchup(a, b) {
  window.location.href = `compare.html?a=${a.id}&b=${b.id}`;
}

document.getElementById('random-matchup').addEventListener('click', () => {
  // Random within whatever's filtered right now (e.g. a category), as
  // long as there are two characters with enough data for a verdict.
  let pool = filteredCharacters().filter((c) => c.scorable);
  if (pool.length < 2) pool = state.characters.filter((c) => c.scorable);
  const i = Math.floor(Math.random() * pool.length);
  let j = Math.floor(Math.random() * (pool.length - 1));
  if (j >= i) j += 1;
  goToMatchup(pool[i], pool[j]);
});

document.getElementById('daily-matchup').addEventListener('click', () => {
  // Same pair for everyone on the same (UTC) day: seeded from the date,
  // over a stable id-ordered pool of verdict-capable characters.
  const pool = state.characters.filter((c) => c.scorable).sort((a, b) => a.id - b.id);
  const day = new Date().toISOString().slice(0, 10);
  let seed = 0;
  for (const ch of day) seed = (seed * 31 + ch.charCodeAt(0)) >>> 0;
  const next = () => {
    seed = (seed * 1103515245 + 12345) >>> 0;
    return seed;
  };
  const i = next() % pool.length;
  let j = next() % (pool.length - 1);
  if (j >= i) j += 1;
  goToMatchup(pool[i], pool[j]);
});

// --- loading ------------------------------------------------------------

async function loadLists() {
  const [categoriesRes, charactersRes] = await Promise.all([Api.listCategories(), Api.listCharacters()]);
  state.categories = categoriesRes;
  state.characters = charactersRes.characters;
}

async function init() {
  try {
    await loadLists();
    // "Compare with..." from a character page arrives as ?with=<id>.
    const withId = Number(new URLSearchParams(location.search).get('with'));
    const pre = state.characters.find((c) => c.id === withId);
    if (pre) state.selected.push({ id: pre.id, name: pre.name, image_url: pre.image_url });
    renderFilterRow();
    renderGrid();
    renderCompareBar();
  } catch (err) {
    emptyState.textContent = 'Could not reach the API: ' + err.message;
    emptyState.style.display = 'block';
  }
}

searchInput.addEventListener('input', (e) => {
  state.query = e.target.value;
  renderGrid();
});
sortSelect.addEventListener('change', (e) => {
  state.sort = e.target.value;
  renderGrid();
});
tierSelect.addEventListener('change', (e) => {
  state.tierGroup = e.target.value;
  renderGrid();
});

// --- Add a character (live fetch from the wiki) -----------------------

const addPanel = document.getElementById('add-character-panel');
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
    await loadLists();
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
