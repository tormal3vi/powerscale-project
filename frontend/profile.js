// Your profile: upload or remove your picture. The picture is shrunk in
// the browser first (phone photos are often 5-10 MB); the server then
// re-encodes whatever arrives into a small square (backend/avatars.py).

renderTopbar([]);

const $ = (id) => document.getElementById(id);
let me = null;

function showError(message) {
  $('profile-error-text').textContent = message || '';
  $('profile-error').style.display = message ? '' : 'none';
}

function render() {
  $('profile-avatar').innerHTML = userAvatarHtml(me.username, me.avatar_url, 'profile-avatar', { admin: me.is_admin });
  $('profile-name').innerHTML = `${escapeHtml(me.username)}${me.is_admin ? ' <span class="admin-badge">Admin</span>' : ''}`;
  $('profile-remove').style.display = me.avatar_url ? '' : 'none';
  $('profile-upload-text').textContent = me.avatar_url ? 'Change photo' : 'Upload photo';
  // Keep the topbar's own avatar in step without a reload.
  _mePromise = Promise.resolve(me);
  renderAccount(document.getElementById('topbar-account'));
}

async function busy(label, work) {
  const text = $('profile-upload-text');
  const before = text.textContent;
  text.textContent = label;
  $('profile-file').disabled = true;
  $('profile-remove').disabled = true;
  showError('');
  try {
    me = await work();
    render();
  } catch (err) {
    text.textContent = before;
    showError(err.message.endsWith('.') ? err.message : `${err.message}.`);
  } finally {
    $('profile-file').disabled = false;
    $('profile-remove').disabled = false;
  }
}

$('profile-file').addEventListener('change', (e) => {
  const file = e.target.files[0];
  e.target.value = ''; // picking the same file again should still fire
  if (file) busy('Uploading…', async () => Api.uploadAvatar(await shrinkImage(file)));
});
$('profile-remove').addEventListener('click', () => busy('Removing…', () => Api.removeAvatar()));

currentUser().then((user) => {
  if (!user) { location.href = 'login.html?next=profile.html'; return; }
  me = user;
  $('profile').style.display = '';
  render();
});
