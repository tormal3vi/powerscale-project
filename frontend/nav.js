// Shared top bar for every page. Desktop: brand, section links, page
// actions, then the account area behind a thin divider. Phone: logo mark,
// the page's title and a menu button that opens the links, actions and
// account as a panel under the bar.

const NAV_LINKS = [
  { href: 'browse.html', label: 'Characters' },
  { href: 'tournament.html', label: 'Tournament' },
  { href: 'board.html', label: 'Board' },
];
const PAGE_TITLES = {
  'browse.html': 'Characters', 'compare.html': 'Compare', 'character.html': 'Character',
  'tournament.html': 'Tournament', 'board.html': 'Board', 'login.html': 'Log in',
};

function renderTopbar(actions = [], { minimal = false } = {}) {
  const bar = document.getElementById('topbar');
  const current = location.pathname.split('/').pop() || 'browse.html';
  bar.className = 'topbar' + (minimal ? ' topbar-minimal' : '');
  const activeHref = current === 'character.html' || current === 'compare.html' ? 'browse.html' : current;
  bar.innerHTML = `
    <a class="topbar-brand" href="browse.html"><span class="topbar-mark"></span><span class="topbar-logo">Powerscale</span></a>
    ${minimal ? '' : `
    <nav class="topbar-nav">
      ${NAV_LINKS.map((l) => `<a href="${l.href}" class="${l.href === activeHref ? 'active' : ''}">${l.label}</a>`).join('')}
    </nav>
    <span class="topbar-title" id="topbar-title">${PAGE_TITLES[current] || ''}</span>
    <button class="topbar-menu-btn" aria-label="Menu" aria-expanded="false">
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M3 6h14M3 10h14M3 14h14" stroke="#D8D0C0" stroke-width="1.4" stroke-linecap="round"/></svg>
    </button>
    <div class="topbar-right">
      <div class="topbar-actions" id="topbar-actions"></div>
      <div class="topbar-account" id="topbar-account"></div>
    </div>`}
  `;
  if (minimal) return;
  const slot = document.getElementById('topbar-actions');
  for (const node of actions) slot.appendChild(node);
  const menuBtn = bar.querySelector('.topbar-menu-btn');
  menuBtn.addEventListener('click', () => {
    const open = bar.classList.toggle('menu-open');
    menuBtn.setAttribute('aria-expanded', String(open));
  });
  renderAccount(document.getElementById('topbar-account'));
}

function setTopbarTitle(text) {
  const el = document.getElementById('topbar-title');
  if (el) el.textContent = text;
}

async function renderAccount(el) {
  const user = await currentUser();
  if (!user) {
    const next = encodeURIComponent(location.pathname.split('/').pop() + location.search);
    el.innerHTML = `<a class="btn-gold btn-sm" href="login.html?next=${next}">Log in</a>`;
    return;
  }
  el.innerHTML = `
    <span class="topbar-divider"></span>
    <span class="topbar-user"><span></span>${user.is_admin ? ' <span class="topbar-role">· admin</span>' : ''}</span>
    <button class="topbar-logout">Log out</button>`;
  el.querySelector('.topbar-user > span').textContent = user.username;
  el.querySelector('.topbar-logout').addEventListener('click', async () => {
    await Api.logout().catch(() => {});
    location.reload();
  });
}

function pillButton(label, { href, id, gold = false } = {}) {
  const el = document.createElement(href ? 'a' : 'button');
  el.className = gold ? 'btn-gold' : 'pill-button';
  if (href) el.href = href;
  if (id) el.id = id;
  el.textContent = label;
  return el;
}
