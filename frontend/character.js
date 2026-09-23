// Character page: character.html?id=<id>. Every form's full stat block,
// abilities and weaknesses, with links out to the wiki and into a
// comparison. All data comes from /api/characters/{id}.

const root = document.getElementById('root');
renderTopbar([]);

// Scored stats show their normalized value (+ peak) and the wiki's own
// wording underneath; the rest are raw wiki text - normalizer.py doesn't
// score them.
const SCORED = [['attack_potency', 'Attack Potency'], ['speed', 'Speed'], ['durability', 'Durability']];
const PLAIN = [['lifting_strength', 'Lifting Strength'], ['striking_strength', 'Striking Strength'],
  ['stamina', 'Stamina'], ['range', 'Range']];
const ABILITIES_PREVIEW = 8;

let character = null;
let formIndex = 0;
let accent = '';

function valueHtml(range, raw, { omnipresent = false } = {}) {
  const value = labelAsWritten(range.baseline_label, raw) || missingStatText(raw);
  const peak = peakHintText(range, raw);
  return `${escapeHtml(value)}${omnipresent ? ' + Omnipresent' : ''}${peak ? ` <span class="cs-peak">(${escapeHtml(peak)})</span>` : ''}`;
}

// Raw wiki text minus its parenthesized justifications - e.g. "At least
// Superhuman (Easily defeated a swordsman ...)" -> "At least Superhuman".
function headline(raw) {
  let depth = 0;
  let out = '';
  for (const ch of raw) {
    if (ch === '(') depth += 1;
    else if (ch === ')') depth = Math.max(0, depth - 1);
    else if (depth === 0) out += ch;
  }
  return out.replace(/\s+([,.;])/g, '$1').replace(/\s+/g, ' ').trim() || raw;
}

function renderStats() {
  const form = character.forms[formIndex];
  document.getElementById('char-tier').textContent = prettifyLabel(form.tier.baseline_label) || '—';
  const rows = [];
  if (form.tier_raw) {
    rows.push(`
      <div class="cs-row">
        <div class="cs-head"><span class="cs-label">Tier</span>
          <span class="cs-value" style="color:${accent}">${valueHtml(form.tier, form.tier_raw)}</span></div>
      </div>`);
  }
  for (const [key, label] of SCORED) {
    const raw = form[`${key}_raw`];
    if (!raw) continue;
    rows.push(`
      <div class="cs-row">
        <div class="cs-head"><span class="cs-label">${label}</span>
          <span class="cs-value">${valueHtml(form[key], raw, { omnipresent: key === 'speed' && form.is_omnipresent })}</span></div>
        <div class="cs-raw">${escapeHtml(raw)}</div>
      </div>`);
  }
  for (const [key, label] of PLAIN) {
    const raw = form[`${key}_raw`];
    if (!raw) continue;
    rows.push(`
      <div class="cs-row cs-plain">
        <span class="cs-plain-label">${label}</span>
        <span class="cs-plain-value" title="${escapeHtml(raw)}">${escapeHtml(headline(raw))}</span>
      </div>`);
  }
  const statsBox = document.getElementById('char-stats');
  statsBox.innerHTML = rows.join('') ||
    '<div class="cs-raw">No stats listed for this form.</div>';
  // Wiki explanations run to whole paragraphs; show three lines and let a
  // click/tap open the rest - only where something was actually cut off.
  statsBox.querySelectorAll('.cs-raw').forEach((el) => {
    el.classList.add('clamped');
    if (el.scrollHeight <= el.clientHeight + 1) { el.classList.remove('clamped'); return; }
    el.classList.add('expandable');
    el.title = 'Click to expand';
    el.addEventListener('click', () => {
      el.classList.toggle('clamped');
      el.title = el.classList.contains('clamped') ? 'Click to expand' : '';
    });
  });

  const pills = document.getElementById('char-forms');
  if (!pills) return;
  pills.innerHTML = '';
  character.forms.forEach((f, i) => {
    const btn = document.createElement('button');
    btn.className = 'form-pill' + (i === formIndex ? ' active' : '');
    btn.textContent = f.name;
    if (i === formIndex) { btn.style.background = accent; btn.style.borderColor = accent; }
    btn.addEventListener('click', () => { formIndex = i; renderStats(); });
    pills.appendChild(btn);
  });
}

function renderAbilities(showAll) {
  const list = character.powers_and_abilities;
  const box = document.getElementById('char-abilities');
  const shown = showAll ? list : list.slice(0, ABILITIES_PREVIEW);
  box.innerHTML = shown.map((a) => `<div class="char-ability">${escapeHtml(a)}</div>`).join('') ||
    '<div class="char-ability-more">None listed.</div>';
  if (!showAll && list.length > ABILITIES_PREVIEW) {
    const more = document.createElement('button');
    more.className = 'char-ability-more';
    more.textContent = `+ ${list.length - ABILITIES_PREVIEW} more…`;
    more.addEventListener('click', () => renderAbilities(true));
    box.appendChild(more);
  }
}

function render() {
  const c = character;
  accent = accentFor(c.id);
  const subtitle = [c.category, c.classification].filter(Boolean).join(' · ');
  const wikiLink = c.source_url.startsWith('http')
    ? `<a class="pill-button" href="${escapeHtml(c.source_url)}" target="_blank" rel="noopener">View on wiki ↗</a>`
    : '';
  root.innerHTML = `
    <div class="char-layout">
      <div class="char-main">
        <div class="char-card char-header">
          <div class="char-header-top">
            <div class="char-avatar" style="background:${accent};">${escapeHtml(initialFor(c.name))}</div>
            <div class="char-header-body">
              <h1 class="char-name">${escapeHtml(c.name)}</h1>
              <div class="char-sub" title="${escapeHtml(subtitle)}">${escapeHtml(subtitle)}</div>
              <div class="char-actions">
                <a class="btn-gold" href="browse.html?with=${c.id}">Compare with…</a>
                ${wikiLink}
              </div>
            </div>
            <div class="char-tier">
              <div class="char-tier-label">Tier</div>
              <div class="char-tier-value" id="char-tier" style="color:${accent};"></div>
            </div>
          </div>
          <div class="char-actions char-actions-phone">
            <a class="btn-gold" href="browse.html?with=${c.id}">Compare with…</a>
            ${wikiLink}
          </div>
          ${c.forms.length > 1 ? `
            <div class="char-forms-wrap">
              <div class="char-section-label">Form</div>
              <div class="char-forms" id="char-forms"></div>
            </div>` : ''}
        </div>
        <div class="char-card">
          <div class="char-section-label char-stats-label">Powers and stats</div>
          <div id="char-stats"></div>
        </div>
      </div>
      <aside class="char-side">
        <div class="char-card char-card-sm">
          <div class="char-card-title">Powers and abilities (${c.powers_and_abilities.length})</div>
          <div class="char-abilities" id="char-abilities"></div>
        </div>
        ${c.weaknesses ? `
          <div class="char-card char-card-sm">
            <div class="char-card-title">Weaknesses</div>
            <div class="char-weak">${escapeHtml(c.weaknesses)}</div>
          </div>` : ''}
      </aside>
    </div>
  `;
  document.title = `${shortName(c.name)} — Powerscale`;
  setTopbarTitle(shortName(c.name));
  renderStats();
  renderAbilities(false);
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
