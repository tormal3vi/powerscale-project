// Matchup cards and the two-sided matchup picker, shared by the Board
// (attaching a matchup to a post) and Duels (picking a challenge's rounds).
// Needs api.js.

function matchupHref(m) {
  const p = new URLSearchParams({ a: m.char_a, b: m.char_b });
  if (m.form_a) p.set('fa', m.form_a);
  if (m.form_b) p.set('fb', m.form_b);
  return `compare.html?${p}`;
}

// "Kratos (God of War)" - the page's own qualifier when it has one
// ("Dante (Devil May Cry)", "Ichigo Kurosaki (Pre-Timeskip)"), else the
// series. The part in brackets is dropped on phones, per the design.
function matchupSideHtml(label, fullName, category) {
  const own = /\(([^)]*)\)/.exec(shortName(fullName));
  const extra = own ? own[1] : category;
  return `${escapeHtml(label)}${extra ? `<span class="mu-series"> (${escapeHtml(extra)})</span>` : ''}`;
}

// The card links to the full comparison; the composer's preview of it
// doesn't (a click there would throw away the half-written post).
function matchupHtml(m, { link = true } = {}) {
  const verdict = m.overruled_winner
    ? `<div class="mu-overruled">${escapeHtml(m.overruled_winner)} wins — overruled<span class="mu-series"> by admins</span></div>
       <div class="mu-calc">Calculator's estimate: ${escapeHtml(m.calc_verdict)}</div>`
    : `<div class="mu-verdict">${escapeHtml(m.calc_verdict)}</div>`;
  const tag = link ? 'a' : 'div';
  return `
    <${tag} class="post-matchup ${m.overruled_winner ? 'overruled' : ''}" ${link ? `href="${matchupHref(m)}"` : ''}>
      <div class="mu-title">${matchupSideHtml(m.label_a, m.name_a, m.category_a)} vs ${matchupSideHtml(m.label_b, m.name_b, m.category_b)}</div>
      ${verdict}
    </${tag}>`;
}

// --- matchup picker ---------------------------------------------------------
// Both sides chosen right in the composer: search (names, aliases, series;
// accent-insensitive), a form dropdown for multi-form characters, and a
// live preview of the exact card the post will carry.

// With verdict: false (Duels) nothing about who'd win is shown - the
// picked matchup is reported as soon as both sides are chosen.
function matchupPickerEl({ onChange, onClose, verdict = true }) {
  const el = document.createElement('div');
  el.className = 'mu-picker';
  el.innerHTML = `
    <div class="mu-picker-head">
      <span class="mu-picker-title">Matchup</span>
      <span class="mu-picker-tools">
        <button type="button" data-act="random">Random</button>
        <button type="button" data-act="remove">Remove</button>
      </span>
    </div>
    <div class="mu-picker-sides">
      <div class="mu-slot" data-side="a"></div>
      <span class="mu-picker-vs">vs</span>
      <div class="mu-slot" data-side="b"></div>
    </div>
    <div class="mu-picker-preview"></div>`;
  const picks = { a: null, b: null }; // {c: full character, formIndex}
  const preview = el.querySelector('.mu-picker-preview');
  const slotEl = (side) => el.querySelector(`.mu-slot[data-side="${side}"]`);
  const other = (side) => picks[side === 'a' ? 'b' : 'a'];
  let seq = 0; // drops stale previews when picks change mid-request

  async function refresh() {
    const my = ++seq;
    if (!picks.a || !picks.b) { preview.innerHTML = ''; onChange(null); return; }
    const fa = picks.a.c.forms[picks.a.formIndex].name;
    const fb = picks.b.c.forms[picks.b.formIndex].name;
    if (!verdict) {
      preview.innerHTML = '';
      onChange({ char_a: picks.a.c.id, char_b: picks.b.c.id, form_a: fa, form_b: fb });
      return;
    }
    preview.innerHTML = '<div class="mu-picker-note">Getting the verdict…</div>';
    onChange(null);
    try {
      const m = await Api.matchupPreview(picks.a.c.id, picks.b.c.id, fa, fb);
      if (my !== seq) return;
      preview.innerHTML = matchupHtml(m, { link: false });
      onChange({ char_a: m.char_a, char_b: m.char_b, form_a: m.form_a, form_b: m.form_b });
    } catch (err) {
      if (my !== seq) return;
      preview.innerHTML = `<div class="form-error">${escapeHtml(err.message)}</div>`;
    }
  }

  async function choose(side, id, formName) {
    slotEl(side).innerHTML = '<div class="mu-picker-note">Loading…</div>';
    try {
      const c = await Api.getCharacter(id);
      let formIndex = formName ? c.forms.findIndex((f) => f.name === formName) : -1;
      if (formIndex < 0) formIndex = defaultFormIndex(c.forms);
      picks[side] = { c, formIndex };
    } catch {
      picks[side] = null; // bad id (e.g. from an old link) - just search instead
    }
    renderSlot(side);
    refresh();
  }

  function renderPicked(slot, side) {
    const { c, formIndex } = picks[side];
    const own = /\(([^)]*)\)/.exec(shortName(c.name));
    slot.innerHTML = `
      <div class="mu-pick">
        <span class="mu-pick-dot" style="background:${accentFor(c.id)}"></span>
        <span class="mu-pick-name">${escapeHtml(bareName(c.name))}<span class="mu-pick-cat"> · ${escapeHtml(own ? own[1] : seriesLabel(c))}</span></span>
        <button type="button" class="mu-pick-clear" aria-label="Change character">×</button>
      </div>
      ${c.forms.length > 1 ? '<select class="mu-pick-form" aria-label="Form"></select>' : ''}`;
    slot.querySelector('.mu-pick-clear').addEventListener('click', () => {
      picks[side] = null;
      renderSlot(side, { focus: true });
      refresh();
    });
    const select = slot.querySelector('select');
    if (select) {
      c.forms.forEach((f, i) => {
        const opt = document.createElement('option');
        opt.value = i;
        opt.textContent = f.name;
        select.appendChild(opt);
      });
      select.value = formIndex;
      select.addEventListener('change', () => { picks[side].formIndex = Number(select.value); refresh(); });
    }
  }

  function renderSearch(slot, side, focus) {
    slot.innerHTML = `
      <input type="text" class="mu-search" autocomplete="off" spellcheck="false"
        placeholder="${side === 'a' ? 'First' : 'Second'} character…" aria-label="${side === 'a' ? 'First' : 'Second'} character">
      <div class="mu-results" role="listbox"></div>`;
    const input = slot.querySelector('input');
    const results = slot.querySelector('.mu-results');
    let items = [];
    let active = 0;
    const paint = () => results.querySelectorAll('.mu-result').forEach((b, i) => b.classList.toggle('active', i === active));
    const show = async () => {
      const q = fold(input.value.trim());
      if (!q) { results.innerHTML = ''; items = []; return; }
      if (!items.length) results.innerHTML = '<div class="mu-picker-note">Searching…</div>';
      const all = await roster();
      if (fold(input.value.trim()) !== q) return; // typed on while loading
      items = searchRoster(all, q, other(side)?.c.id);
      active = 0;
      results.innerHTML = items.length
        ? items.map((c, i) => `
            <button type="button" class="mu-result" role="option" data-i="${i}">
              <span class="mu-result-name">${escapeHtml(shortName(c.name))}</span>
              <span class="mu-result-cat">${escapeHtml(seriesLabel(c))}</span>
            </button>`).join('')
        : '<div class="mu-picker-note">No characters match that.</div>';
      paint();
    };
    input.addEventListener('input', show);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!items.length) return;
        e.preventDefault();
        active = (active + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
        paint();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (items[active]) choose(side, items[active].id);
      }
    });
    results.addEventListener('click', (e) => {
      const b = e.target.closest('.mu-result');
      if (b) choose(side, items[Number(b.dataset.i)].id);
    });
    if (focus) input.focus();
  }

  function renderSlot(side, { focus = false } = {}) {
    const slot = slotEl(side);
    if (picks[side]) renderPicked(slot, side);
    else renderSearch(slot, side, focus);
  }

  el.querySelector('[data-act="random"]').addEventListener('click', async () => {
    const pool = (await roster()).filter((c) => c.scorable);
    const a = pool[Math.floor(Math.random() * pool.length)];
    let b = a;
    while (b.id === a.id) b = pool[Math.floor(Math.random() * pool.length)];
    picks.a = null; picks.b = null;
    await Promise.all([choose('a', a.id), choose('b', b.id)]);
  });
  el.querySelector('[data-act="remove"]').addEventListener('click', () => {
    seq += 1;
    onChange(null);
    onClose();
  });

  renderSlot('a');
  renderSlot('b');
  el.prefill = (a, b, fa, fb) => Promise.all([choose('a', a, fa), choose('b', b, fb)]);
  el.focusFirst = () => slotEl('a').querySelector('input')?.focus();
  return el;
}
