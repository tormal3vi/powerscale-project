// Compare screen. URL: compare.html?a=<id>&b=<id> (built by browse.js's
// "Compare ->" button). Renders the two character cards, radar chart,
// stat table and verdict panel entirely from real API data - nothing
// here is sample/placeholder.

const AXES = ['attack_potency', 'speed', 'durability', 'tier'];
const AXIS_LABELS = { attack_potency: 'Attack Potency', speed: 'Speed', durability: 'Durability', tier: 'Tier' };
// top / right / bottom / left, matching the mockup's radar layout exactly.
const AXIS_DIR = { attack_potency: [0, -1], speed: [1, 0], durability: [0, 1], tier: [-1, 0] };

const state = {
  a: null, // CharacterDetailOut
  b: null,
  formIndexA: 0,
  formIndexB: 0,
  verdict: null,
};

const root = document.getElementById('root');

function formIndexByName(forms, name) {
  if (!name) return null;
  const idx = forms.findIndex((f) => f.name === name);
  return idx === -1 ? null : idx;
}

function syncUrl() {
  // Keep the address bar a shareable link to exactly this matchup,
  // including the selected forms.
  const params = new URLSearchParams({ a: state.a.id, b: state.b.id });
  if (state.a.forms.length > 1) params.set('fa', activeForm('a').name);
  if (state.b.forms.length > 1) params.set('fb', activeForm('b').name);
  history.replaceState(null, '', `compare.html?${params}`);
}

function activeForm(side) {
  return side === 'a' ? state.a.forms[state.formIndexA] : state.b.forms[state.formIndexB];
}

// --- data loading -----------------------------------------------------

async function loadCharacters() {
  const params = new URLSearchParams(window.location.search);
  const idA = params.get('a');
  const idB = params.get('b');
  if (!idA || !idB) {
    root.innerHTML = '<div class="error-state">No characters selected. <a href="browse.html" style="color:var(--accent-gold);">Go pick two from Browse →</a></div>';
    return;
  }

  const [a, b] = await Promise.all([Api.getCharacter(idA), Api.getCharacter(idB)]);
  state.a = a;
  state.b = b;
  // A shared link carries the chosen forms (fa/fb) - honor them when
  // they still exist, otherwise fall back to the usual default form.
  state.formIndexA = formIndexByName(a.forms, params.get('fa')) ?? defaultFormIndex(a.forms);
  state.formIndexB = formIndexByName(b.forms, params.get('fb')) ?? defaultFormIndex(b.forms);
  document.title = `${shortName(a.name)} vs ${shortName(b.name)} — Powerscale`;

  buildLayout();
  await refreshComparison();
}

async function refreshComparison() {
  const formA = activeForm('a');
  const formB = activeForm('b');
  syncUrl();
  try {
    state.verdict = await Api.compare(state.a.id, state.b.id, formA.name, formB.name);
  } catch (err) {
    state.verdict = null;
    document.getElementById('verdict-card').innerHTML =
      `<div class="error-state">Couldn't compute a verdict: ${err.message}</div>`;
    return;
  }
  renderHeroTiers();
  renderRadar();
  renderStatTable();
  renderVerdict();
}

// --- static layout (built once characters are loaded) ----------------

function buildLayout() {
  const [accentA, accentB] = accentPair(state.a.id, state.b.id);

  root.innerHTML = `
    <div class="vs-hero">
      ${heroCardHtml('a', state.a, accentA)}
      <div class="vs-mark">
        <div class="vs-mark-word">vs</div>
        <div class="vs-mark-line"></div>
      </div>
      ${heroCardHtml('b', state.b, accentB)}
    </div>
    <div class="analysis-row">
      <div class="radar-card">
        <div class="section-label">Normalized stat comparison</div>
        <svg viewBox="0 0 440 440" width="440" height="440" id="radar-svg">
          <polygon points="220,180 260,220 220,260 180,220" fill="none" stroke="#2E291F" stroke-width="1"/>
          <polygon points="220,140 300,220 220,300 140,220" fill="none" stroke="#2E291F" stroke-width="1"/>
          <polygon points="220,100 340,220 220,340 100,220" fill="none" stroke="#2E291F" stroke-width="1"/>
          <polygon points="220,60 380,220 220,380 60,220" fill="none" stroke="#3A352C" stroke-width="1.4"/>
          <line x1="220" y1="60" x2="220" y2="380" stroke="#2E291F" stroke-width="1"/>
          <line x1="60" y1="220" x2="380" y2="220" stroke="#2E291F" stroke-width="1"/>
          <g id="radar-data"></g>
          <text x="220" y="40" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="12" fill="#A69C8C">ATTACK POTENCY</text>
          <text x="398" y="225" text-anchor="start" font-family="IBM Plex Mono, monospace" font-size="12" fill="#A69C8C">SPEED</text>
          <text x="220" y="408" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="12" fill="#A69C8C">DURABILITY</text>
          <text x="42" y="225" text-anchor="end" font-family="IBM Plex Mono, monospace" font-size="12" fill="#A69C8C">TIER</text>
        </svg>
        <div class="radar-legend">
          <div class="radar-legend-item"><span class="radar-legend-swatch" style="background:${accentA};"></span>${escapeHtml(state.a.name)}</div>
          <div class="radar-legend-item"><span class="radar-legend-swatch" style="background:${accentB};"></span>${escapeHtml(state.b.name)}</div>
        </div>
        <div class="radar-caption">Scaled per-axis for this matchup, not an absolute scale. A missing stat plots at the center, not as zero.</div>
      </div>
      <div class="stat-table-card">
        <div class="section-label">Raw stat breakdown</div>
        <div id="stat-rows"></div>
      </div>
    </div>
    <div class="verdict-section">
      <div class="verdict-card" id="verdict-card"></div>
    </div>
  `;

  bindFormPills('a');
  bindFormPills('b');
}

function heroCardHtml(side, char, accent) {
  const form = char.forms[side === 'a' ? state.formIndexA : state.formIndexB];
  const showForms = char.forms.length > 1;
  const abilities = char.powers_and_abilities || [];
  const VISIBLE_ABILITIES = 8;

  return `
    <div class="vs-card">
      <div class="vs-card-head">
        <div class="avatar avatar-lg" style="background:${accent};">${escapeHtml(initialFor(char.name))}</div>
        <div class="vs-card-ident">
          <a class="vs-card-name" href="character.html?id=${char.id}" title="${escapeHtml(char.name)}">${escapeHtml(char.name)}</a>
          <div class="vs-card-subtitle">${escapeHtml(char.category)}${char.classification ? ' · ' + escapeHtml(char.classification) : ''}</div>
        </div>
        <div class="vs-card-tier">
          <div class="vs-card-tier-label">Tier</div>
          <div class="vs-card-tier-value" style="color:${accent};" id="tier-value-${side}" title="${escapeHtml(form.tier_raw || '')}">${escapeHtml(prettifyLabel(form.tier.baseline_label) || '—')}</div>
          <div class="vs-card-tier-peak" id="tier-peak-${side}">${peakHintHtml(form.tier, form.tier_raw)}</div>
        </div>
      </div>
      ${showForms ? `
        <div>
          <div class="section-label">Form</div>
          <div class="pill-row" id="form-pills-${side}"></div>
        </div>
      ` : ''}
      <div class="ability-row" id="ability-row-${side}">
        ${abilities.slice(0, VISIBLE_ABILITIES).map((a) => abilityPillHtml(a, accent)).join('')}
        ${abilities.length > VISIBLE_ABILITIES ? `<div class="ability-pill more" id="ability-more-${side}">+${abilities.length - VISIBLE_ABILITIES} more</div>` : ''}
      </div>
    </div>
  `;
}

function abilityPillHtml(text, accent) {
  // Purely a visual cue, not a re-derivation of calculator.py's own
  // ability-flag matching: a dot shows on any pill whose own text
  // contains one of the same curated keywords calculator.ability_flags()
  // looks for. The verdict panel's caveat box is the authoritative
  // "these tags actually applied" source - this is decoration on top.
  const flagged = ABILITY_KEYWORDS.some((kw) => text.toLowerCase().includes(kw));
  const short = text.length > 60 ? text.slice(0, 57) + '…' : text;
  return `<div class="ability-pill" title="${escapeHtml(text)}">${flagged ? `<span class="ability-dot" style="background:${accent};"></span>` : ''}${escapeHtml(short)}</div>`;
}

const ABILITY_KEYWORDS = [
  'regeneration', 'immortality', 'reality warping', 'acausality', 'non-corporeal',
  'bfr', 'battlefield removal', 'existence erasure', 'petrification',
  'durability negation', 'ignores durability', 'one-hit-kill', 'instant death',
  'probability manipulation', 'resistance to',
];

function bindFormPills(side) {
  const char = side === 'a' ? state.a : state.b;
  const container = document.getElementById(`form-pills-${side}`);
  if (!container) return;
  const accent = accentPair(state.a.id, state.b.id)[side === 'a' ? 0 : 1];
  const activeIdx = side === 'a' ? state.formIndexA : state.formIndexB;

  container.innerHTML = '';
  char.forms.forEach((f, i) => {
    const btn = document.createElement('button');
    btn.className = 'form-pill' + (i === activeIdx ? ' active' : '');
    btn.textContent = f.name;
    if (i === activeIdx) { btn.style.background = accent; btn.style.borderColor = accent; }
    btn.addEventListener('click', async () => {
      if (side === 'a') state.formIndexA = i; else state.formIndexB = i;
      bindFormPills(side);
      await refreshComparison();
    });
    container.appendChild(btn);
  });

  const moreBtn = document.getElementById(`ability-more-${side}`);
  if (moreBtn) {
    moreBtn.addEventListener('click', () => {
      const abilities = char.powers_and_abilities || [];
      const row = document.getElementById(`ability-row-${side}`);
      row.innerHTML = abilities.map((a) => abilityPillHtml(a, accent)).join('');
    });
  }
}

function renderHeroTiers() {
  const formA = activeForm('a');
  const formB = activeForm('b');
  const tierValueA = document.getElementById('tier-value-a');
  const tierValueB = document.getElementById('tier-value-b');
  tierValueA.textContent = prettifyLabel(formA.tier.baseline_label) || '—';
  tierValueA.title = formA.tier_raw || '';
  tierValueB.textContent = prettifyLabel(formB.tier.baseline_label) || '—';
  tierValueB.title = formB.tier_raw || '';
  document.getElementById('tier-peak-a').innerHTML = peakHintHtml(formA.tier, formA.tier_raw);
  document.getElementById('tier-peak-b').innerHTML = peakHintHtml(formB.tier, formB.tier_raw);
}

// --- radar + stat table (driven by verdict.axis_comparisons) --------------

function axisScale(comp) {
  // Per-axis relative scaling for this one matchup - see the radar
  // caption. Returns {ta, tb} each in [0, 1] (0 = center/no data).
  const { a_value: a, b_value: b } = comp;
  if (a === null && b === null) return { ta: 0, tb: 0 };
  if (a === null) return { ta: 0, tb: 0.6 };
  if (b === null) return { ta: 0.6, tb: 0 };
  if (a === b) return { ta: 0.65, tb: 0.65 };
  const min = Math.min(a, b);
  const max = Math.max(a, b);
  const frac = (v) => 0.15 + 0.85 * ((v - min) / (max - min));
  return { ta: frac(a), tb: frac(b) };
}

function axisPoint(axis, t) {
  const [dx, dy] = AXIS_DIR[axis];
  const radius = 160;
  return [220 + dx * t * radius, 220 + dy * t * radius];
}

function renderRadar() {
  const byAxis = {};
  for (const c of state.verdict.axis_comparisons) byAxis[c.axis] = c;

  const ptsA = [], ptsB = [];
  for (const axis of AXES) {
    const comp = byAxis[axis];
    const { ta, tb } = axisScale(comp);
    ptsA.push(axisPoint(axis, ta));
    ptsB.push(axisPoint(axis, tb));
  }

  const [accentA, accentB] = accentPair(state.a.id, state.b.id);
  const toPoints = (pts) => pts.map((p) => p.join(',')).join(' ');

  document.getElementById('radar-data').innerHTML = `
    <polygon points="${toPoints(ptsA)}" fill="${accentA}" fill-opacity="0.16" stroke="${accentA}" stroke-width="2"/>
    <polygon points="${toPoints(ptsB)}" fill="${accentB}" fill-opacity="0.16" stroke="${accentB}" stroke-width="2"/>
  `;
}

function renderStatTable() {
  const byAxis = {};
  for (const c of state.verdict.axis_comparisons) byAxis[c.axis] = c;
  const [accentA, accentB] = accentPair(state.a.id, state.b.id);
  const formA = activeForm('a');
  const formB = activeForm('b');

  const rowsHtml = AXES.map((axis) => {
    const comp = byAxis[axis];
    const { ta, tb } = axisScale(comp);
    const rawLabelA = axis === 'tier' ? formA.tier.baseline_label : formA[axis].baseline_label;
    const rawLabelB = axis === 'tier' ? formB.tier.baseline_label : formB[axis].baseline_label;
    const textA = comp.a_value === null ? missingStatText(formA[`${axis}_raw`]) : (prettifyLabel(rawLabelA) || '—');
    const textB = comp.b_value === null ? missingStatText(formB[`${axis}_raw`]) : (prettifyLabel(rawLabelB) || '—');
    // The badge shows only the (deliberately conservative) baseline value -
    // see calculator.py's select_form() docstring - so a character whose
    // wiki entry splits low/high across two different conditions (e.g.
    // "Street level physically, Island level with magic") can look weaker
    // here than their page actually describes. The full raw wiki phrasing
    // is always available in the API response, so surface it as a hover
    // tooltip rather than silently dropping the peak side of the range.
    const rawTextA = formA[`${axis}_raw`] || '';
    const rawTextB = formB[`${axis}_raw`] || '';
    const rangeA = axis === 'tier' ? formA.tier : formA[axis];
    const rangeB = axis === 'tier' ? formB.tier : formB[axis];

    return `
      <div class="stat-row">
        <div class="stat-row-label">${AXIS_LABELS[axis]}</div>
        <div class="stat-row-grid">
          <div>
            <div class="stat-value-text" title="${escapeHtml(rawTextA)}">${escapeHtml(textA)}</div>
            ${peakHintHtml(rangeA, rawTextA)}
            <div class="stat-bar-track"><div class="stat-bar-fill" style="width:${ta * 100}%; background:${accentA};"></div></div>
          </div>
          <div>
            <div class="stat-value-text" title="${escapeHtml(rawTextB)}">${escapeHtml(textB)}</div>
            ${peakHintHtml(rangeB, rawTextB)}
            <div class="stat-bar-track"><div class="stat-bar-fill" style="width:${tb * 100}%; background:${accentB};"></div></div>
          </div>
        </div>
      </div>
    `;
  }).join('');

  document.getElementById('stat-rows').innerHTML = rowsHtml;
}

// --- verdict panel ----------------------------------------------------

function reasonBullets(v) {
  const byAxis = {};
  for (const c of v.axis_comparisons) byAxis[c.axis] = c;
  const nameA = shortName(v.character_a);
  const nameB = shortName(v.character_b);
  const lines = [];
  for (const axis of AXES) {
    const c = byAxis[axis];
    if (c.advantage === null) {
      // Say WHOSE stat is missing - "unscored on one side" read as if
      // the character you were looking at was the one without it.
      // Group by why it's missing: "Unknown" on the wiki reads very
      // differently from a value the site couldn't score.
      const groups = {};
      [['a', nameA, c.a_value], ['b', nameB, c.b_value]].forEach(([side, name, value]) => {
        if (value !== null) return;
        const why = missingStatText(activeForm(side)[`${axis}_raw`]);
        const phrase = why === 'Unknown' ? 'Unknown on the wiki' : why === 'Not listed' ? 'not listed' : 'unscored';
        (groups[phrase] = groups[phrase] || []).push(name);
      });
      const parts = Object.entries(groups).map(([phrase, names]) => `${phrase} for ${names.join(' and ')}`);
      lines.push(`${AXIS_LABELS[axis]} isn't compared — ${parts.join('; ')}`);
      continue;
    }
    const mag = Math.abs(c.advantage);
    const leader = c.advantage > 0 ? nameA : nameB;
    if (mag < 0.1) lines.push(`${AXIS_LABELS[axis]} is close between both`);
    else if (mag < 0.5) lines.push(`${AXIS_LABELS[axis]} edges toward ${leader}`);
    else lines.push(`${AXIS_LABELS[axis]} strongly favors ${leader}`);
  }
  if (v.axes_used === AXES.length) lines.push('No axis is missing from this comparison');
  return lines;
}

function overrideBannerHtml(v) {
  if (!v.override) return '';
  const o = v.override;
  const date = new Date(o.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  return `
    <div class="override-banner">
      ${o.note ? `<div class="override-note">“${escapeHtml(o.note)}”</div>` : ''}
      <div class="override-meta">— ${escapeHtml(o.admin)}, admin · ${escapeHtml(date)} · <a href="board.html">Discuss on the Board</a></div>
    </div>`;
}

async function renderAdminPanel(card) {
  const user = await currentUser();
  if (!user || !user.is_admin || !state.verdict) return;
  const v = state.verdict;
  const panel = document.createElement('div');
  panel.className = 'admin-panel';
  panel.innerHTML = `
    <div class="admin-head">
      <svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M8 1.5 14 4.5v4c0 4-2.7 6.5-6 8-3.3-1.5-6-4-6-8v-4L8 1.5Z" stroke="#D9A441" stroke-width="1.3" stroke-linejoin="round"/></svg>
      <span>Admin panel</span>
      <span class="admin-scope">applies to these two forms only · saving posts it to the Board</span>
    </div>
    <div class="admin-row">
      <label><input type="radio" name="ov-winner" value="${state.a.id}"> <span></span></label>
      <label><input type="radio" name="ov-winner" value="${state.b.id}"> <span></span></label>
    </div>
    <label class="admin-reason">
      <span>Reason</span>
      <textarea class="admin-note" rows="2" maxlength="300"></textarea>
    </label>
    <div class="admin-row admin-buttons">
      <button class="btn-gold" data-act="save">${v.override ? 'Update overrule' : 'Save overrule'}</button>
      ${v.override ? '<button class="pill-button" data-act="remove">Remove overrule</button>' : ''}
    </div>
    <div class="add-character-status error"></div>`;
  const [spanA, spanB] = panel.querySelectorAll('.admin-row label span');
  spanA.textContent = `${bareName(v.character_a)} wins`;
  spanB.textContent = `${bareName(v.character_b)} wins`;
  if (v.override) {
    panel.querySelector(`input[value="${v.override.winner_id}"]`).checked = true;
    panel.querySelector('.admin-note').value = v.override.note;
  }
  const err = panel.querySelector('.add-character-status');
  panel.addEventListener('click', async (e) => {
    const act = e.target.dataset && e.target.dataset.act;
    if (!act) return;
    err.textContent = '';
    try {
      if (act === 'save') {
        const picked = panel.querySelector('input[name="ov-winner"]:checked');
        if (!picked) { err.textContent = 'Pick who wins first.'; return; }
        await Api.setOverride(state.a.id, state.b.id, v.form_a, v.form_b, Number(picked.value), panel.querySelector('.admin-note').value);
      } else {
        await Api.removeOverride(state.a.id, state.b.id, v.form_a, v.form_b);
      }
      await refreshComparison();
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
  card.appendChild(panel);
}

function renderVerdict() {
  const v = state.verdict;
  const card = document.getElementById('verdict-card');
  const overruledHeadline = v.override ? `${escapeHtml(shortName(v.override.winner_name))} wins — overruled by admins` : null;

  if (v.composite === null) {
    card.innerHTML = `
      <div class="verdict-head">
        <div>
          <div class="section-label">Who would win?</div>
          <div class="verdict-headline">${overruledHeadline || 'Insufficient data'}</div>
        </div>
      </div>
      ${overrideBannerHtml(v)}
      <div style="color:var(--text-secondary); font-size:14px;">
        Only ${v.axes_used} of 4 stats are comparable between these two forms — too little for the calculator to give a meaningful verdict rather than a guess.
      </div>
      ${notesHtml(v)}
    `;
    renderAdminPanel(card);
    return;
  }

  const favored = v.favored;
  const magPct = Math.min(Math.abs(v.composite), 1) * 50; // half-track max
  const leansLeft = favored === v.character_a; // A is drawn on the left
  const fillLeft = leansLeft ? 50 - magPct : 50;
  const fillWidth = magPct;

  const [accentA, accentB] = accentPair(state.a.id, state.b.id);
  const fillColor = leansLeft ? accentA : accentB;

  const calcHeadline = `${favored ? escapeHtml(favored) + ' favored — ' : ''}${escapeHtml(v.label)}${v.confidence_hint !== 'n/a' ? ' (' + v.confidence_hint + ')' : ''}`;
  const calcShort = `${favored ? escapeHtml(shortName(favored)) + ' favored — ' : ''}${escapeHtml(v.label)}`;
  card.innerHTML = `
    <div class="verdict-head">
      <div>
        <div class="section-label">Who would win?</div>
        <div class="verdict-headline">${overruledHeadline || calcHeadline}</div>
      </div>
      ${v.override ? '' : '<div class="verdict-disclaimer">Heuristic estimate from normalized stats — not a calibrated probability.</div>'}
    </div>
    ${overrideBannerHtml(v)}
    ${v.override ? `<div class="calc-estimate">Calculator's estimate: ${calcShort}</div>` : ''}
    <div class="${v.override ? 'meter-muted' : ''}">
      <div class="verdict-meter-track">
        <div class="verdict-meter-fill" style="left:${fillLeft}%; width:${fillWidth}%; background:${fillColor};"></div>
        <div class="verdict-meter-center"></div>
      </div>
      <div class="verdict-meter-labels"><span>${escapeHtml(v.character_a)}</span><span>${escapeHtml(v.character_b)}</span></div>
    </div>
    <div class="verdict-reasons">${reasonBullets(v).map((r) => `<div>— ${escapeHtml(r)}</div>`).join('')}</div>
    ${v.partial_data ? `<div class="verdict-callout"><div class="verdict-callout-text">Based on partial data — ${v.axes_used}/4 stats were comparable, so this can't reach the top confidence band.</div></div>` : ''}
    ${abilityCalloutHtml(v)}
    ${notesHtml(v)}
  `;
  renderAdminPanel(card);
}

function abilityCalloutHtml(v) {
  if (!v.ability_flags.length) return '';
  const summary = v.ability_flags.map((f) => `${f.tag} (${f.characters.join(', ')})`).join(', ');
  return `
    <div class="verdict-callout">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" style="flex-shrink:0;margin-top:2px;"><path d="M8 1.5 15 14H1L8 1.5Z" stroke="#D9A441" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.2v3.4M8 11.6v.1" stroke="#D9A441" stroke-width="1.3" stroke-linecap="round"/></svg>
      <div class="verdict-callout-text">Ability flags — ${escapeHtml(summary)} — are shown above for context only. They aren't reflected in the score above.</div>
    </div>
  `;
}

function notesHtml(v) {
  if (!v.notes.length) return '';
  return `<ul class="verdict-notes">${v.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join('')}</ul>`;
}


const copyLinkBtn = pillButton('Copy link');
copyLinkBtn.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(location.href);
    copyLinkBtn.textContent = 'Link copied';
  } catch {
    copyLinkBtn.textContent = 'Copy failed';
  }
  setTimeout(() => { copyLinkBtn.textContent = 'Copy link'; }, 1800);
});
const shareBtn = pillButton('Share to board');
shareBtn.addEventListener('click', () => {
  if (!state.a || !state.b) return;
  const p = new URLSearchParams({ a: state.a.id, b: state.b.id, fa: activeForm('a').name, fb: activeForm('b').name });
  location.href = `board.html?${p}`;
});
renderTopbar([copyLinkBtn, shareBtn, pillButton('Change characters', { href: 'browse.html' })]);

loadCharacters().catch((err) => {
  root.innerHTML = `<div class="error-state">Failed to load: ${escapeHtml(err.message)}</div>`;
});
