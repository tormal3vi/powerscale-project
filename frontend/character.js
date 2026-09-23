// Character page: character.html?id=<id>. Every form's full stat block,
// abilities and weaknesses, with links out to the wiki and into a
// comparison. All data comes from /api/characters/{id}.

const root = document.getElementById('root');
renderTopbar([pillButton('All characters', { href: 'browse.html' })]);

// Scored stats show their normalized label (+ peak hint); the rest are
// raw wiki text only - normalizer.py doesn't score them.
const STAT_ROWS = [
  ['tier', 'Tier', true],
  ['attack_potency', 'Attack Potency', true],
  ['speed', 'Speed', true],
  ['durability', 'Durability', true],
  ['lifting_strength', 'Lifting Strength', false],
  ['striking_strength', 'Striking Strength', false],
  ['stamina', 'Stamina', false],
  ['range', 'Range', false],
];

let character = null;
let formIndex = 0;

function statRowHtml(form, key, label, scored) {
  const raw = form[`${key}_raw`];
  if (!raw) return '';
  let headline = '';
  if (scored) {
    const range = form[key];
    headline = `
      <div class="char-stat-value">${escapeHtml(prettifyLabel(range.baseline_label) || 'Unscored')}${key === 'speed' && form.is_omnipresent ? ' + Omnipresent' : ''}</div>
      ${peakHintHtml(range, raw)}`;
  }
  return `
    <div class="char-stat-row">
      <div class="char-stat-label">${label}</div>
      <div>
        ${headline}
        <div class="char-stat-raw">${escapeHtml(raw)}</div>
      </div>
    </div>`;
}

function renderForm() {
  const form = character.forms[formIndex];
  document.getElementById('char-tier').textContent = prettifyLabel(form.tier.baseline_label) || '—';
  document.getElementById('char-stats').innerHTML =
    STAT_ROWS.map(([key, label, scored]) => statRowHtml(form, key, label, scored)).join('') ||
    '<div class="char-stat-raw">No stats listed for this form.</div>';
  const pills = document.getElementById('char-forms');
  if (!pills) return;
  pills.innerHTML = '';
  character.forms.forEach((f, i) => {
    const btn = document.createElement('button');
    btn.className = 'form-pill' + (i === formIndex ? ' active' : '');
    btn.textContent = f.name;
    if (i === formIndex) { btn.style.background = accentFor(character.id); btn.style.borderColor = accentFor(character.id); }
    btn.addEventListener('click', () => { formIndex = i; renderForm(); });
    pills.appendChild(btn);
  });
}

function render() {
  const c = character;
  const accent = accentFor(c.id);
  const subtitle = [c.category, c.classification].filter(Boolean).join(' · ');
  const wikiLink = c.source_url.startsWith('http')
    ? `<a class="pill-button" href="${escapeHtml(c.source_url)}" target="_blank" rel="noopener">View on wiki ↗</a>`
    : '';
  root.innerHTML = `
    <div class="char-hero">
      <div class="vs-card-head">
        <div class="avatar avatar-lg" style="background:${accent};">${escapeHtml(initialFor(c.name))}</div>
        <div style="min-width:0;">
          <h1 class="char-name">${escapeHtml(c.name)}</h1>
          <div class="vs-card-subtitle">${escapeHtml(subtitle)}</div>
          ${c.origin && c.origin !== c.category ? `<div class="vs-card-subtitle">Origin: ${escapeHtml(c.origin)}</div>` : ''}
        </div>
        <div class="vs-card-tier">
          <div class="vs-card-tier-label">Tier</div>
          <div class="vs-card-tier-value" style="color:${accent};" id="char-tier"></div>
        </div>
      </div>
      <div class="char-actions">
        <a class="compare-bar-cta" href="browse.html?with=${c.id}">Compare with…</a>
        ${wikiLink}
      </div>
      ${c.forms.length > 1 ? `<div><div class="section-label">Form</div><div class="pill-row" id="char-forms"></div></div>` : ''}
    </div>

    <div class="char-grid">
      <div class="stat-table-card">
        <div class="section-label">Stats</div>
        <div id="char-stats"></div>
      </div>
      <div class="char-side">
        <div class="stat-table-card">
          <div class="section-label">Powers and abilities (${c.powers_and_abilities.length})</div>
          <ul class="char-abilities">${c.powers_and_abilities.map((a) => `<li>${escapeHtml(a)}</li>`).join('') || '<li>None listed.</li>'}</ul>
        </div>
        ${c.weaknesses ? `<div class="stat-table-card"><div class="section-label">Weaknesses</div><div class="char-stat-raw">${escapeHtml(c.weaknesses)}</div></div>` : ''}
      </div>
    </div>
  `;
  document.title = `${shortName(c.name)} — Powerscale`;
  renderForm();
}

async function init() {
  const id = new URLSearchParams(location.search).get('id');
  if (!id) {
    root.innerHTML = '<div class="error-state">No character selected. <a href="browse.html" style="color:var(--accent-gold);">Browse characters →</a></div>';
    return;
  }
  try {
    character = await Api.getCharacter(id);
    formIndex = defaultFormIndex(character.forms);
    render();
  } catch (err) {
    root.innerHTML = `<div class="error-state">Failed to load: ${escapeHtml(err.message)}</div>`;
  }
}

init();
