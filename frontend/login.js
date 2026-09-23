// Log in / create account. On success, go back to ?next= (only ever a page
// of this site - an open redirect to anywhere would be a phishing aid).

renderTopbar([], { minimal: true });

let mode = 'login';
const $ = (id) => document.getElementById(id);

function safeNext() {
  const next = new URLSearchParams(location.search).get('next') || '';
  return /^[a-z]+\.html(\?[^#]*)?$/i.test(next) ? next : 'board.html';
}

function showError(message) {
  $('auth-error-text').textContent = message || '';
  $('auth-error').style.display = message ? '' : 'none';
}

function setMode(m) {
  mode = m;
  document.querySelectorAll('.auth-tab').forEach((t) => t.classList.toggle('active', t.dataset.mode === m));
  $('auth-submit').textContent = m === 'login' ? 'Log in' : 'Create account';
  $('auth-password').autocomplete = m === 'login' ? 'current-password' : 'new-password';
  showError('');
}

document.querySelectorAll('.auth-tab').forEach((t) => t.addEventListener('click', () => setMode(t.dataset.mode)));

$('auth-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = $('auth-username').value.trim();
  const password = $('auth-password').value;
  $('auth-submit').disabled = true;
  showError('');
  try {
    if (mode === 'login') await Api.login(username, password);
    else await Api.register(username, password);
    location.href = safeNext();
  } catch (err) {
    showError(err.message.endsWith('.') ? err.message : `${err.message}.`);
  } finally {
    $('auth-submit').disabled = false;
  }
});

currentUser().then((user) => {
  if (user) location.href = safeNext();
});
setMode(new URLSearchParams(location.search).get('mode') === 'register' ? 'register' : 'login');
