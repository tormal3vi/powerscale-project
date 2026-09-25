// Profile settings: picture, details (username, bio, favorite character),
// password, other sessions, and deleting the account. Each section saves
// on its own and shows its own errors next to the field they're about.

renderTopbar([], { back: true });

const $ = (id) => document.getElementById(id);
const BIO_MAX = 160;
const ERROR_ICON = '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="6.5" stroke="#E15252" stroke-width="1.3"/><path d="M8 5v3.5M8 10.8v.1" stroke="#E15252" stroke-width="1.3" stroke-linecap="round"/></svg>';
const CHECK_ICON = '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="6.5" stroke="#8FBF6B" stroke-width="1.4"/><path d="m5.3 8.2 1.8 1.8 3.6-4" stroke="#8FBF6B" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const SEARCH_ICON = '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="7" cy="7" r="5" stroke="#7A7264" stroke-width="1.4"/><path d="m11 11 3.5 3.5" stroke="#7A7264" stroke-width="1.4" stroke-linecap="round"/></svg>';
const SPINNER = '<svg class="spinner" width="100%" height="100%" viewBox="0 0 56 56" aria-hidden="true"><circle cx="28" cy="28" r="24" fill="none" stroke="#3A352C" stroke-width="3"/><path d="M28 4a24 24 0 0 1 24 24" fill="none" stroke="#D9A441" stroke-width="3" stroke-linecap="round"/></svg>';

let profile = null;   // from /api/me/profile
let favorite = null;  // {id, name, image_url} or null - what Save will send

// --- small helpers -------------------------------------------------------------

function fieldError(id, message, input) {
  const el = $(id);
  el.innerHTML = message ? `${ERROR_ICON}<span></span>` : '';
  if (message) el.querySelector('span').textContent = message.replace(/\.?$/, '.');
  el.hidden = !message;
  if (input) input.classList.toggle('invalid', !!message);
}

function flashSaved(id, text = 'Saved') {
  const el = $(id);
  el.innerHTML = `${CHECK_ICON}<span></span>`;
  el.querySelector('span').textContent = text;
  el.hidden = false;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.hidden = true; }, 4000);
}

// Keep the topbar (avatar, name) in step with edits, without a reload.
function refreshTopbar() {
  _mePromise = Promise.resolve({ username: profile.username, is_admin: profile.is_admin, avatar_url: profile.avatar_url });
  renderAccount(document.getElementById('topbar-account'));
}

// --- header + picture ----------------------------------------------------------

function renderHeader() {
  $('head-avatar').innerHTML = userAvatarHtml(profile.username, profile.avatar_url, 'settings-avatar', { admin: profile.is_admin });
  $('head-name').textContent = profile.username;
  $('head-badge').innerHTML = profile.is_admin ? adminBadgeHtml({ solid: true }) : '';
  const since = monthYear(profile.member_since);
  const posts = `${profile.post_count} post${profile.post_count === 1 ? '' : 's'}`;
  const likes = `${profile.likes_received} like${profile.likes_received === 1 ? '' : 's'}`;
  $('head-stats').innerHTML = `
    <span class="desktop-only">Member since ${since} · ${posts} · ${likes} received</span>
    <span class="phone-only">${since} · ${posts} · ${likes}</span>`;
  $('pw-username').value = profile.username; // lets password managers file the new password correctly
}

function renderPicture() {
  $('pic-tile').innerHTML = userAvatarHtml(profile.username, profile.avatar_url, 'pic-tile');
  $('pic-remove').disabled = !profile.avatar_url;
}

function setUploading(pct) {
  const uploading = pct !== null;
  $('pic-controls').hidden = uploading;
  $('pic-progress').hidden = !uploading;
  if (uploading) {
    $('pic-tile').innerHTML = `<div class="pic-tile pic-tile-busy">${SPINNER}</div>`;
    $('pic-pct').textContent = `${pct}%`;
  }
}

$('pic-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = ''; // picking the same file again should still fire
  if (!file) return;
  fieldError('pic-error', '');
  setUploading(0);
  try {
    const user = await Api.uploadAvatar(await shrinkImage(file), (pct) => { $('pic-pct').textContent = `${pct}%`; });
    profile.avatar_url = user.avatar_url;
  } catch (err) {
    fieldError('pic-error', err.message);
  }
  setUploading(null);
  renderPicture();
  renderHeader();
  refreshTopbar();
});

$('pic-remove').addEventListener('click', async () => {
  fieldError('pic-error', '');
  try {
    await Api.removeAvatar();
    profile.avatar_url = null;
  } catch (err) {
    fieldError('pic-error', err.message);
  }
  renderPicture();
  renderHeader();
  refreshTopbar();
});

// --- details: username, bio, favorite character --------------------------------

function updateBioCount() {
  $('bio-count').textContent = `${$('f-bio').value.length}/${BIO_MAX}`;
}
$('f-bio').addEventListener('input', updateBioCount);

function renderFavorite() {
  const slot = $('fav-slot');
  if (favorite) {
    slot.innerHTML = `
      <span class="fav-chip">
        <span class="fav-chip-tile" style="background:${accentFor(favorite.id)}">${characterTileInner(favorite.name, favorite.image_url, 52)}</span>
        <span class="fav-chip-name"></span>
        <button type="button" class="fav-chip-clear" aria-label="Change favorite character">×</button>
      </span>`;
    const tile = slot.querySelector('.fav-chip-tile');
    tile.classList.toggle('has-pic', !!favorite.image_url);
    slot.querySelector('.fav-chip-name').textContent = favorite.name;
    slot.querySelector('.fav-chip-clear').addEventListener('click', () => {
      favorite = null;
      renderFavorite();
      slot.querySelector('input').focus();
    });
    return;
  }
  slot.innerHTML = `
    <div class="fav-search">
      <label class="fav-search-box">${SEARCH_ICON}
        <input type="text" placeholder="Search characters…" autocomplete="off" spellcheck="false" aria-labelledby="fav-label">
      </label>
      <div class="fav-results" role="listbox"></div>
    </div>`;
  const input = slot.querySelector('input');
  const results = slot.querySelector('.fav-results');
  let items = [];
  let active = 0;
  const paint = () => results.querySelectorAll('.fav-result').forEach((b, i) => b.classList.toggle('active', i === active));
  const choose = (c) => {
    favorite = { id: c.id, name: bareName(c.name), image_url: c.image_url };
    renderFavorite();
  };
  input.addEventListener('focus', () => roster()); // start the ~1600-character fetch early
  input.addEventListener('input', async () => {
    const q = fold(input.value.trim());
    if (!q) { results.innerHTML = ''; items = []; return; }
    const all = await roster();
    if (fold(input.value.trim()) !== q) return; // typed on while loading
    items = searchRoster(all, q, null, 6);
    active = 0;
    results.innerHTML = items.length
      ? items.map((c, i) => `
          <button type="button" class="fav-result" role="option" data-i="${i}">
            <span class="fav-result-tile${c.image_url ? ' has-pic' : ''}" style="background:${accentFor(c.id)}">${characterTileInner(c.name, c.image_url, 64)}</span>
            <span class="fav-result-text"><span class="fav-result-name">${escapeHtml(shortName(c.name))}</span><span class="fav-result-cat">${escapeHtml(seriesLabel(c))}</span></span>
          </button>`).join('')
      : '<div class="fav-empty">No characters match that.</div>';
    paint();
  });
  input.addEventListener('keydown', (e) => {
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && items.length) {
      e.preventDefault();
      active = (active + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
      paint();
    } else if (e.key === 'Enter') {
      e.preventDefault(); // don't submit the whole form from the search box
      if (items[active]) choose(items[active]);
    }
  });
  results.addEventListener('click', (e) => {
    const b = e.target.closest('.fav-result');
    if (b) choose(items[Number(b.dataset.i)]);
  });
}

function fillDetails() {
  $('f-username').value = profile.username;
  $('f-username').readOnly = profile.is_admin;
  $('username-hint').classList.toggle('keep', profile.is_admin);
  $('username-hint').textContent = profile.is_admin
    ? "Admins can't change their username - admin access is tied to it."
    : '3–20 letters, numbers or underscores';
  $('f-bio').value = profile.bio;
  updateBioCount();
  favorite = profile.favorite;
  renderFavorite();
}

$('details-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = $('f-username').value.trim();
  fieldError('username-error', '', $('f-username'));
  fieldError('details-error', '');
  if (!/^[A-Za-z0-9_]{3,20}$/.test(username)) {
    fieldError('username-error', 'Use 3–20 letters, numbers or underscores', $('f-username'));
    return;
  }
  const btn = e.submitter || $('details-form').querySelector('button[type=submit]');
  btn.disabled = true;
  try {
    const saved = await Api.saveProfile({ username, bio: $('f-bio').value, favorite_char_id: favorite ? favorite.id : null });
    profile = { ...profile, ...saved };
    fillDetails();
    renderHeader();
    refreshTopbar();
    flashSaved('details-saved');
  } catch (err) {
    if (/username/i.test(err.message)) fieldError('username-error', err.message, $('f-username'));
    else fieldError('details-error', err.message);
  } finally {
    btn.disabled = false;
  }
});

// --- password ------------------------------------------------------------------

$('password-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const [cur, next, confirm] = [$('f-current'), $('f-new'), $('f-confirm')];
  fieldError('current-error', '', cur);
  fieldError('new-error', '', next);
  fieldError('confirm-error', '', confirm);
  if (!cur.value) { fieldError('current-error', 'Enter your current password', cur); return; }
  if (next.value.length < 8) { fieldError('new-error', 'Use at least 8 characters', next); return; }
  if (next.value !== confirm.value) { fieldError('confirm-error', "Passwords don't match", confirm); return; }
  const btn = e.submitter || $('password-form').querySelector('button[type=submit]');
  btn.disabled = true;
  try {
    const r = await Api.changePassword(cur.value, next.value);
    [cur, next, confirm].forEach((i) => { i.value = ''; });
    flashSaved('password-saved', r.other_sessions_ended
      ? `Password updated - ${r.other_sessions_ended} other device${r.other_sessions_ended === 1 ? '' : 's'} logged out`
      : 'Password updated');
  } catch (err) {
    if (/current|wrong/i.test(err.message)) fieldError('current-error', err.message, cur);
    else fieldError('new-error', err.message, next);
  } finally {
    btn.disabled = false;
  }
});

// --- sessions ------------------------------------------------------------------

$('logout-others').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    const r = await Api.logoutOtherDevices();
    flashSaved('sessions-saved', r.ended
      ? `Logged out of ${r.ended} other device${r.ended === 1 ? '' : 's'}`
      : 'No other devices were logged in');
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
});

// --- delete account ------------------------------------------------------------

function setDeleteOpen(open) {
  $('delete-modal').hidden = !open;
  document.body.classList.toggle('modal-open', open);
  fieldError('delete-error', '', $('f-delete-password'));
  $('f-delete-password').value = '';
  if (open) $('f-delete-password').focus();
  else $('delete-open').focus();
}

$('delete-open').addEventListener('click', () => setDeleteOpen(true));
$('delete-cancel').addEventListener('click', () => setDeleteOpen(false));
$('delete-modal').addEventListener('click', (e) => { if (e.target === e.currentTarget) setDeleteOpen(false); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('delete-modal').hidden) setDeleteOpen(false); });

$('delete-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const pw = $('f-delete-password');
  if (!pw.value) { fieldError('delete-error', 'Enter your password', pw); return; }
  $('delete-confirm').disabled = true;
  try {
    await Api.deleteAccount(pw.value);
    location.href = 'board.html';
  } catch (err) {
    fieldError('delete-error', err.message, pw);
    $('delete-confirm').disabled = false;
  }
});

// --- load ------------------------------------------------------------------------

(async () => {
  const user = await currentUser();
  if (!user) { location.href = 'login.html?next=profile.html'; return; }
  try {
    profile = await Api.myProfile();
  } catch (err) {
    document.querySelector('.page').insertAdjacentHTML('beforeend',
      `<div class="error-state">Couldn't load your profile: ${escapeHtml(err.message)}</div>`);
    return;
  }
  $('settings').style.display = '';
  renderHeader();
  renderPicture();
  fillDetails();
})();
