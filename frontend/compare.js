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
  renderComments(); // per character pair, so not redone when forms change
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
    renderTicketBox();
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
        <div class="vs-mark-line vs-mark-lead"></div>
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
      <div id="ticket-slot"></div>
    </div>
    <section class="mc-card" id="matchup-comments" aria-labelledby="mc-title"></section>
  `;

  bindFormPills('a');
  bindFormPills('b');
  // Phones hide each fighter's abilities behind this toggle (the phone
  // design keeps both fighters on one screen); desktop never shows it.
  root.querySelectorAll('.ability-toggle').forEach((btn) => btn.addEventListener('click', () => {
    const open = btn.closest('.vs-card').classList.toggle('abilities-open');
    btn.setAttribute('aria-expanded', String(open));
  }));
}

function heroCardHtml(side, char, accent) {
  const form = char.forms[side === 'a' ? state.formIndexA : state.formIndexB];
  const showForms = char.forms.length > 1;
  const abilities = char.powers_and_abilities || [];
  const VISIBLE_ABILITIES = 8;

  return `
    <div class="vs-card" style="--accent:${accent};">
      <div class="vs-card-head">
        <div class="avatar avatar-lg${(form.image_url || char.image_url) ? ' has-pic' : ''}" id="hero-avatar-${side}" style="background:${accent};">${characterTileInner(char.name, form.image_url || char.image_url, 128)}</div>
        <div class="vs-card-ident">
          <a class="vs-card-name" href="character.html?id=${char.id}" title="${escapeHtml(char.name)}">${escapeHtml(char.name)}</a>
          <div class="vs-card-subtitle">${escapeHtml(seriesLabel(char))}<span class="vs-card-class">${char.classification ? ' · ' + escapeHtml(char.classification) : ''}</span></div>
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
      ${abilities.length ? `<button type="button" class="ability-toggle" aria-expanded="false">Powers and abilities (${abilities.length})</button>` : ''}
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
  // On phones the forms are one sideways-scrolling row; start it at the
  // selected form rather than leaving that off-screen.
  const active = container.querySelector('.form-pill.active');
  if (active && container.scrollWidth > container.clientWidth) {
    container.scrollLeft = Math.max(0, active.offsetLeft - container.offsetLeft - 16);
  }

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
  // A form with its own picture (Giorno's Requiem) swaps the hero's.
  for (const [side, form] of [['a', formA], ['b', formB]]) {
    const char = state[side];
    const url = form.image_url || char.image_url;
    const el = document.getElementById(`hero-avatar-${side}`);
    if (el && el.dataset.pic !== (url || '')) {
      el.dataset.pic = url || '';
      setCharacterTile(el, char.name, url, 128);
    }
  }
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
      <svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M8 1.2 14 3.8v3.6c0 3.7-2.5 6.1-6 7.4-3.5-1.3-6-3.7-6-7.4V3.8L8 1.2Z" stroke="#D9A441" stroke-width="1.3" stroke-linejoin="round"/></svg>
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

// --- tickets --------------------------------------------------------------
// Anyone who disagrees with this exact verdict (these two forms) can ask
// the admins to look at it: one ticket per matchup, ever.

const TICKET_MAX = 1000;

// A small card under the verdict: a ticket to the admins for users who
// disagree. Quiet (one line) until opened; one ticket per exact matchup.
let ticketRun = 0;
async function renderTicketBox() {
  const slot = document.getElementById('ticket-slot');
  const v = state.verdict;
  if (!slot) return;
  const run = ++ticketRun;
  slot.innerHTML = '';
  if (!v) return;
  const user = await currentUser();
  if (run !== ticketRun || (user && user.is_admin)) return; // admins overrule directly
  const box = document.createElement('div');
  box.className = 'ticket-box';
  const show = () => { if (run === ticketRun) slot.replaceChildren(box); };
  if (!user) {
    const next = encodeURIComponent(location.pathname.split('/').pop() + location.search);
    box.innerHTML = `<a href="login.html?next=${next}">Log in</a> to send a ticket if you disagree with this verdict.`;
    show();
    return;
  }
  let mine;
  try {
    mine = await Api.myTicket(state.a.id, state.b.id, v.form_a, v.form_b);
  } catch { return; }
  if (state.verdict !== v) return; // forms changed while loading
  const nameOf = (id) => bareName(id === state.a.id ? v.character_a : v.character_b);
  if (mine.ticket) {
    const t = mine.ticket;
    const said = `you said ${escapeHtml(nameOf(t.winner_id))} wins`;
    if (t.status === 'open') {
      box.innerHTML = `<b>Open</b> — ${said}. An admin will look at it; the answer shows up here.`;
    } else if (t.outcome === 'overruled') {
      box.classList.add('overruled');
      box.innerHTML = `<div><b>Overruled</b> — an admin agreed (${said}) and credited you.</div>
        ${t.response ? '<div class="ticket-reply"></div>' : ''}<a href="board.html">See the Board post</a>`;
    } else {
      box.innerHTML = `<div><b>Kept</b> — the verdict stands (${said}).</div>${t.response ? '<div class="ticket-reply"></div>' : ''}`;
    }
    const reply = box.querySelector('.ticket-reply');
    if (reply) reply.textContent = `“${t.response}” — ${t.admin || 'admin'}`;
    show();
    return;
  }
  if (mine.banned) {
    box.textContent = "You've been restricted from sending tickets.";
    show();
    return;
  }
  const collapsed = () => {
    box.className = 'ticket-box ticket-invite';
    box.innerHTML = '<span>Think this verdict’s wrong?</span><button type="button" class="ticket-btn">Send a ticket</button>';
    box.querySelector('button').addEventListener('click', openForm);
  };
  const openForm = () => {
    box.className = 'ticket-box ticket-form';
    box.innerHTML = `
      <div class="ticket-q">Who should win? <span>These exact forms · one ticket per matchup</span></div>
      <div class="ticket-pick" role="radiogroup" aria-label="Who should win">
        <button type="button" role="radio" aria-checked="false" data-w="${state.a.id}">${escapeHtml(nameOf(state.a.id))}</button>
        <button type="button" role="radio" aria-checked="false" data-w="${state.b.id}">${escapeHtml(nameOf(state.b.id))}</button>
      </div>
      <label><span class="visually-hidden">Why</span>
        <textarea rows="3" maxlength="${TICKET_MAX}" placeholder="Why? Feats, scans, wiki pages… (up to ${TICKET_MAX} characters)"></textarea></label>
      <div class="ticket-foot"><span class="form-error ticket-error"></span><span class="ticket-count">0/${TICKET_MAX}</span></div>
      <div class="ticket-actions">
        <button type="button" class="ticket-btn" data-act="cancel">Cancel</button>
        <button type="button" class="btn-gold" data-act="send">Send ticket</button>
      </div>`;
    let winner = null;
    const ta = box.querySelector('textarea');
    const err = box.querySelector('.ticket-error');
    box.querySelectorAll('[data-w]').forEach((b) => b.addEventListener('click', () => {
      winner = Number(b.dataset.w);
      box.querySelectorAll('[data-w]').forEach((x) => x.setAttribute('aria-checked', String(x === b)));
      err.textContent = '';
    }));
    ta.addEventListener('input', () => { box.querySelector('.ticket-count').textContent = `${ta.value.length}/${TICKET_MAX}`; });
    box.querySelector('[data-act="cancel"]').addEventListener('click', collapsed);
    box.querySelector('[data-act="send"]').addEventListener('click', async (e) => {
      if (!winner) { err.textContent = 'Pick who should win.'; return; }
      if (ta.value.trim().length < 10) { err.textContent = 'Say a bit more about why.'; return; }
      e.target.disabled = true;
      try {
        await Api.sendTicket({ char_a: state.a.id, char_b: state.b.id, form_a: v.form_a, form_b: v.form_b,
          winner_id: winner, reason: ta.value.trim() });
        renderTicketBox();
      } catch (ex) {
        err.textContent = ex.message;
        e.target.disabled = false;
      }
    });
  };
  collapsed();
  show();
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
    renderTicketBox();
    return;
  }

  const favored = v.favored;
  const magPct = Math.min(Math.abs(v.composite), 1) * 50; // half-track max
  const leansLeft = favored === v.character_a; // A is drawn on the left
  const fillLeft = leansLeft ? 50 - magPct : 50;
  const fillWidth = magPct;

  const [accentA, accentB] = accentPair(state.a.id, state.b.id);
  const fillColor = leansLeft ? accentA : accentB;

  const calcHeadline = `${favored ? escapeHtml(shortName(favored)) + ' favored — ' : ''}${escapeHtml(v.label)}${v.confidence_hint !== 'n/a' ? ' (' + v.confidence_hint + ')' : ''}`;
  const calcShort = `${favored ? escapeHtml(shortName(favored)) + ' favored — ' : ''}${escapeHtml(v.label)}`;
  card.innerHTML = `
    <div class="verdict-head">
      <div>
        <div class="section-label">Who would win?</div>
        <div class="verdict-headline">${overruledHeadline || calcHeadline}</div>
      </div>
      ${v.override ? '' : '<div class="verdict-disclaimer">Heuristic estimate from normalized stats — not a calibrated probability. <a href="faq.html#verdict">How it\'s worked out</a></div>'}
    </div>
    ${overrideBannerHtml(v)}
    ${v.override ? `<div class="calc-estimate">Calculator's estimate: ${calcShort}</div>` : ''}
    <div class="${v.override ? 'meter-muted' : ''}">
      <div class="verdict-meter-track">
        <div class="verdict-meter-fill" style="left:${fillLeft}%; width:${fillWidth}%; background:${fillColor};"></div>
        <div class="verdict-meter-center"></div>
      </div>
      <div class="verdict-meter-labels"><span>${escapeHtml(shortName(v.character_a))}</span><span>${escapeHtml(shortName(v.character_b))}</span></div>
    </div>
    <div class="verdict-reasons">${reasonBullets(v).map((r) => `<div>— ${escapeHtml(r)}</div>`).join('')}</div>
    ${v.partial_data ? `<div class="verdict-callout"><div class="verdict-callout-text">Based on partial data — ${v.axes_used}/4 stats were comparable, so this can't reach the top confidence band.</div></div>` : ''}
    ${abilityCalloutHtml(v)}
    ${notesHtml(v)}
  `;
  renderAdminPanel(card);
  renderTicketBox();
}

function abilityCalloutHtml(v) {
  if (!v.ability_flags.length) return '';
  const summary = v.ability_flags.map((f) => `${f.tag} (${f.characters.join(', ')})`).join(', ');
  return `
    <div class="verdict-callout">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" style="flex-shrink:0;margin-top:2px;"><path d="M8 1.5 15 14H1L8 1.5Z" stroke="#D9A441" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.2v3.4M8 11.6v.1" stroke="#D9A441" stroke-width="1.3" stroke-linecap="round"/></svg>
      <div class="verdict-callout-text">Ability flags — ${escapeHtml(summary)} — are shown above for context only. They aren't reflected in the score above. <a href="faq.html#abilities">Why?</a></div>
    </div>
  `;
}

function notesHtml(v) {
  if (!v.notes.length) return '';
  return `<ul class="verdict-notes">${v.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join('')}</ul>`;
}


// --- sharing ------------------------------------------------------------
// Share ▾: copy the link, Reddit, X, the phone's share sheet; challenge a
// friend to a duel on this matchup; and, set apart at the bottom, save
// the matchup as an image for posting.

function verdictLines(v) {
  if (v.override) return [`${shortName(v.override.winner_name)} wins`, 'Overruled by admins'];
  if (v.composite === null) return ['Not enough data', 'for a verdict'];
  if (v.favored) return [`${shortName(v.favored)} favored`, `${v.label}${v.confidence_hint !== 'n/a' ? ` (${v.confidence_hint})` : ''}`];
  return ['Too close to call', 'A toss-up'];
}

function shareInfo() {
  if (!state.a || !state.b || !state.verdict) return null;
  const nameA = shortName(state.a.name);
  const nameB = shortName(state.b.name);
  const [first, second] = verdictLines(state.verdict);
  return {
    url: location.href,
    title: `${nameA} vs ${nameB}: who would win?`,
    text: `${nameA} vs ${nameB}: ${first}${second ? ` (${second.toLowerCase()})` : ''}. Agree?`,
  };
}

async function challengeFriend(button) {
  const user = await currentUser();
  if (!user) {
    location.href = `login.html?next=${encodeURIComponent(location.pathname.split('/').pop() + location.search)}`;
    return;
  }
  const nameA = shortName(state.a.name);
  const nameB = shortName(state.b.name);
  button.disabled = true;
  button.textContent = 'Creating…';
  try {
    const g = await Api.createGame('1v1', [], [{ char_a: state.a.id, char_b: state.b.id,
      form_a: activeForm('a').name, form_b: activeForm('b').name }], [], 'predict', true);
    const url = `${location.origin}${location.pathname.replace(/[^/]*$/, '')}duels.html?game=${g.id}`;
    let shared = false;
    if (navigator.share) {
      try {
        await navigator.share({ title: 'Duel me on Powerscale', text: `Can you call ${nameA} vs ${nameB}? Five matchups, 20 seconds each.`, url });
        shared = true;
      } catch { /* dismissed: fall back to copying */ }
    }
    let copied = false;
    if (!shared) {
      try { await navigator.clipboard.writeText(url); copied = true; } catch { /* shown below */ }
    }
    const note = document.createElement('div');
    note.className = 'share-note';
    note.innerHTML = `Challenge ready${copied ? ', link copied' : ''}. ${shared ? 'Sent!' : 'Send it to a friend:'} this matchup is one of the five rounds. <a href="duels.html?game=${g.id}">Open it</a>`;
    button.replaceWith(note);
  } catch (err) {
    button.textContent = /clear winner/.test(err.message) ? 'Too close to call for a duel round' : err.message;
  }
}

// Square for posts, or 9:16 (story) for TikTok, Shorts, Reels and stories:
// the same card, with the two characters stacked instead of side by side.
async function saveMatchupImage({ story = false } = {}) {
  const W = 1080;
  const H = story ? 1920 : 1080;
  const L = story ? {
    head: 175, S: 430, tiles: [[W / 2, 250], [W / 2, 950]], nameDy: 490, vs: [W / 2, 905], textMax: 900,
    glow: [[W / 2, 470], [W / 2, 1170]], verdict: 1630, second: 1680, meter: 1724, logo: 1800,
  } : {
    head: 110, S: 380, tiles: [[270, 170], [W - 270, 170]], nameDy: 450, vs: [W / 2, 380], textMax: 440,
    glow: [[0, 420], [W, 420]], verdict: 800, second: 846, meter: 884, logo: 972,
  };
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const x = canvas.getContext('2d');
  await Promise.all(['700 64px Fraunces', '600 30px "Public Sans"', '600 26px "IBM Plex Mono"']
    .map((f) => document.fonts.load(f).catch(() => null)));
  const [accentA, accentB] = accentPair(state.a.id, state.b.id);
  const load = (src) => new Promise((res) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = () => res(null);
    img.src = src;
  });
  // Served from this site (see /api/characters/{id}/picture): a picture
  // straight from the wiki would make the canvas impossible to save.
  const picture = (side) => `api/characters/${state[side].id}/picture?px=400&form=${encodeURIComponent(activeForm(side).name)}`;
  const [imgA, imgB, logo] = await Promise.all([load(picture('a')), load(picture('b')), load('favicon.svg?v=2')]);

  x.fillStyle = '#14120F';
  x.fillRect(0, 0, W, H);
  for (const [[cx, cy], color] of [[L.glow[0], accentA], [L.glow[1], accentB]]) {  // a soft glow behind each side
    const glow = x.createRadialGradient(cx, cy, 0, cx, cy, 620);
    glow.addColorStop(0, color);
    glow.addColorStop(1, 'transparent');
    x.globalAlpha = 0.16;
    x.fillStyle = glow;
    x.fillRect(0, 0, W, H);
    x.globalAlpha = 1;
  }
  const text = (str, px, cx, y, { font = '"Public Sans"', weight = 600, color = '#F3EEE4', max = 460, spacing = 0 } = {}) => {
    let size = px;
    if ('letterSpacing' in x) x.letterSpacing = `${spacing}px`;
    do {
      x.font = `${weight} ${size}px ${font}, sans-serif`;
      size -= 2;
    } while (x.measureText(str).width > max && size > 14);
    x.fillStyle = color;
    x.textAlign = 'center';
    x.fillText(str, cx, y);
    if ('letterSpacing' in x) x.letterSpacing = '0px';
  };
  text('WHO WOULD WIN?', story ? 40 : 28, W / 2, L.head, { font: '"IBM Plex Mono"', color: '#D9A441', max: 900, spacing: 6 });

  const S = L.S;
  const sides = [['a', imgA, accentA, ...L.tiles[0]], ['b', imgB, accentB, ...L.tiles[1]]];
  for (const [side, img, color, cx, top] of sides) {
    const left = cx - S / 2;
    x.save();
    x.beginPath();
    x.roundRect(left, top, S, S, 36);
    x.clip();
    x.fillStyle = color;
    x.fillRect(left, top, S, S);
    if (img) x.drawImage(img, left, top, S, S);
    else text(initialFor(state[side].name), 170, cx, top + S / 2 + 60, { font: 'Fraunces', weight: 700, color: '#14120F' });
    x.restore();
    x.lineWidth = 6;
    x.strokeStyle = color;
    x.beginPath();
    x.roundRect(left, top, S, S, 36);
    x.stroke();
    const form = activeForm(side);
    const character = state[side];
    const nameY = top + L.nameDy;
    text(shortName(character.name), story ? 52 : 42, cx, nameY, { font: 'Fraunces', weight: 700, max: L.textMax });
    const series = character.subseries ? `${character.category} · ${character.subseries}` : character.category;
    text(series, story ? 26 : 22, cx, nameY + 40, { font: '"IBM Plex Mono"', color: '#A69C8C', max: L.textMax });
    const detail = [character.forms.length > 1 ? form.name : null, prettifyLabel(form.tier.baseline_label) ? `Tier ${prettifyLabel(form.tier.baseline_label)}` : null]
      .filter(Boolean).join(' · ');
    if (detail) text(detail, story ? 26 : 22, cx, nameY + 76, { color: color, max: L.textMax });
  }
  text('VS', story ? 72 : 60, L.vs[0], L.vs[1], { font: 'Fraunces', weight: 700, color: '#D9A441' });

  const v = state.verdict;
  const [first, second] = verdictLines(v);
  text(first, story ? 60 : 54, W / 2, L.verdict, { font: 'Fraunces', weight: 700, max: 960 });
  if (second) text(second, story ? 32 : 28, W / 2, L.second, { color: '#D8D0C0', max: 960 });
  // The verdict meter, as on the page: from the middle toward whoever leads.
  const trackW = 760;
  const trackX = (W - trackW) / 2;
  x.fillStyle = '#2B2720';
  x.beginPath();
  x.roundRect(trackX, L.meter, trackW, 14, 7);
  x.fill();
  const lean = v.override ? (v.override.winner_id === state.a.id ? 1 : -1) : Math.max(-1, Math.min(1, v.composite || 0));
  if (lean) {
    const w = Math.abs(lean) * trackW / 2;
    x.fillStyle = lean > 0 ? accentA : accentB;
    x.beginPath();
    x.roundRect(lean > 0 ? W / 2 - w : W / 2, L.meter, w, 14, 7);
    x.fill();
  }
  x.fillStyle = '#F3EEE4';
  x.fillRect(W / 2 - 1.5, L.meter - 6, 3, 26);

  // The logo, then the address, centered together.
  x.font = '600 30px "IBM Plex Mono", monospace';
  const site = 'powerscale.online';
  const start = (W - (44 + 16 + x.measureText(site).width)) / 2;
  if (logo) x.drawImage(logo, start, L.logo, 44, 44);
  x.fillStyle = '#A69C8C';
  x.textAlign = 'left';
  x.fillText(site, start + 60, L.logo + 32);

  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  const slug = (str) => str.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'character';
  const name = `${slug(shortName(state.a.name))}-vs-${slug(shortName(state.b.name))}${story ? '-story' : ''}.png`;
  const file = new File([blob], name, { type: 'image/png' });
  if (matchMedia('(pointer: coarse)').matches && navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: shareInfo().title }); return; } catch { /* dismissed: download instead */ }
  }
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 5000);
}

const shareMenuBtn = pillButton('Share ▾');
const boardBtn = pillButton('Post to Board');
boardBtn.addEventListener('click', () => {
  if (!state.a || !state.b) return;
  const p = new URLSearchParams({ a: state.a.id, b: state.b.id, fa: activeForm('a').name, fb: activeForm('b').name });
  location.href = `board.html?${p}`;
});
renderTopbar([shareMenuBtn, boardBtn, pillButton('Change characters', { href: 'browse.html' })]);
attachShareMenu(shareMenuBtn, shareInfo, [
  { label: 'Challenge a friend', onClick: (b) => challengeFriend(b) },
  { label: 'Save as image', quiet: true, onClick: async (b, close) => {
    b.textContent = 'Drawing…';
    try { await saveMatchupImage(); close(); } catch { b.textContent = "Couldn't make the image"; }
    setTimeout(() => { b.textContent = 'Save as image'; }, 1500);
  } },
  { label: 'Save for TikTok / stories (9:16)', quiet: true, onClick: async (b, close) => {
    b.textContent = 'Drawing…';
    try { await saveMatchupImage({ story: true }); close(); } catch { b.textContent = "Couldn't make the image"; }
    setTimeout(() => { b.textContent = 'Save for TikTok / stories (9:16)'; }, 1500);
  } },
]);

// --- comments -----------------------------------------------------------
// This matchup's own discussion, separate from the Board: shared by A vs B
// and B vs A, whichever forms are picked. Newest first, composer on top.

async function renderComments() {
  const box = document.getElementById('matchup-comments');
  box.innerHTML = `
    <div class="mc-head"><h2 class="section-label" id="mc-title">Comments</h2><span class="mc-count"></span></div>
    <div class="mc-compose"></div>
    <div class="mc-list"><div class="mc-note">Loading comments…</div></div>`;
  const list = box.querySelector('.mc-list');
  const count = box.querySelector('.mc-count');
  const [user, res] = await Promise.all([currentUser(), Api.matchupComments(state.a.id, state.b.id).catch((e) => e)]);
  let comments = res instanceof Error ? null : res.comments.slice().reverse();

  const paint = () => {
    if (!comments) { list.innerHTML = `<div class="form-error">${escapeHtml(res.message)}</div>`; return; }
    count.textContent = comments.length ? String(comments.length) : '';
    list.innerHTML = comments.length ? '' : `<div class="mc-note">No comments yet. Who takes this one, and why?</div>`;
    for (const c of comments) {
      const el = document.createElement('div');
      el.className = 'mc-item';
      el.innerHTML = `
        ${userAvatarHtml(c.author, c.author_avatar, 'mc-avatar', { admin: c.author_is_admin })}
        <div class="mc-main">
          <div class="mc-meta"><a class="mc-author" href="user.html?u=${encodeURIComponent(c.author)}">${escapeHtml(c.author)}</a>${c.author_is_admin ? adminBadgeHtml() : ''}${titleHtml(c.author_title)}
            <span class="mc-time" title="${escapeHtml(new Date(c.created_at).toLocaleString())}">· ${timeAgo(c.created_at)}</span>
            ${c.can_delete ? '<button type="button" class="mc-delete">Delete</button>' : ''}</div>
          <div class="mc-body"></div>
        </div>`;
      el.querySelector('.mc-body').textContent = c.body;
      const del = el.querySelector('.mc-delete');
      if (del) del.addEventListener('click', async () => {
        if (!confirm('Delete this comment?')) return;
        try {
          await Api.deleteMatchupComment(c.id);
          comments = comments.filter((x) => x.id !== c.id);
          paint();
        } catch (err) { alert(err.message); }
      });
      list.appendChild(el);
    }
  };
  paint();

  const compose = box.querySelector('.mc-compose');
  if (!user) {
    const next = encodeURIComponent(location.pathname.split('/').pop() + location.search);
    compose.innerHTML = `<div class="mc-login"><a href="login.html?next=${next}">Log in</a> to comment on this matchup.</div>`;
    return;
  }
  compose.innerHTML = `
    <form class="mc-form">
      <label class="visually-hidden" for="mc-input">Write a comment</label>
      <textarea id="mc-input" rows="2" maxlength="500" placeholder="Who wins, and why?"></textarea>
      <div class="mc-form-row"><span class="form-error mc-error"></span><button class="btn-gold mc-post">Comment</button></div>
    </form>`;
  const form = compose.querySelector('form');
  const input = form.querySelector('textarea');
  const btn = form.querySelector('button');
  const err = form.querySelector('.mc-error');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = input.value.trim();
    if (!body) return;
    btn.disabled = true;
    err.textContent = '';
    try {
      const c = await Api.addMatchupComment(state.a.id, state.b.id, body);
      input.value = '';
      comments = [c, ...(comments || [])];
      paint();
    } catch (e2) {
      err.textContent = e2.message;
    } finally {
      btn.disabled = false;
    }
  });
}

loadCharacters().catch((err) => {
  root.innerHTML = `<div class="error-state">Failed to load: ${escapeHtml(err.message)}</div>`;
});
