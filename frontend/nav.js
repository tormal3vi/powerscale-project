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
  'profile.html': 'Profile settings',
};

const MENU_ICON = '<svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M3 6h14M3 10h14M3 14h14" stroke="#D8D0C0" stroke-width="1.4" stroke-linecap="round"/></svg>';
const CLOSE_ICON = '<svg width="18" height="18" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="#D8D0C0" stroke-width="1.5" stroke-linecap="round"/></svg>';
const PERSON_ICON = '<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="5.5" r="2.5" stroke="#D8D0C0" stroke-width="1.3"/><path d="M3 13c0-2.5 2.2-4 5-4s5 1.5 5 4" stroke="#D8D0C0" stroke-width="1.3" stroke-linecap="round"/></svg>';
const LOGOUT_ICON = '<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M6 3H3v10h3M10 5l3 3-3 3M13 8H6" stroke="#A69C8C" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// `back`: on phones, a back arrow replaces the logo and menu (Profile
// settings is a place you step into and out of).
function renderTopbar(actions = [], { minimal = false, back = false } = {}) {
  const bar = document.getElementById('topbar');
  const current = location.pathname.split('/').pop() || 'browse.html';
  bar.className = 'topbar' + (minimal ? ' topbar-minimal' : '') + (back ? ' topbar-back-mode' : '');
  const activeHref = current === 'character.html' || current === 'compare.html' ? 'browse.html' : current;
  bar.innerHTML = `
    ${back ? `<button class="topbar-back" aria-label="Back">
      <svg width="18" height="18" viewBox="0 0 16 16" fill="none"><path d="M10 3 5 8l5 5" stroke="#F3EEE4" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>` : ''}
    <a class="topbar-brand" href="browse.html"><span class="topbar-mark"></span><span class="topbar-logo">Powerscale</span></a>
    ${minimal ? '' : `
    <nav class="topbar-nav">
      ${NAV_LINKS.map((l) => `<a href="${l.href}" class="${l.href === activeHref ? 'active' : ''}">${l.label}</a>`).join('')}
    </nav>
    <span class="topbar-title" id="topbar-title">${PAGE_TITLES[current] || ''}</span>
    <button class="topbar-menu-btn" aria-label="Menu" aria-expanded="false">${MENU_ICON}</button>
    ${back ? '<span class="topbar-back-spacer"></span>' : ''}
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
    menuBtn.setAttribute('aria-label', open ? 'Close menu' : 'Menu');
    menuBtn.innerHTML = open ? CLOSE_ICON : MENU_ICON;
  });
  const backBtn = bar.querySelector('.topbar-back');
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      // Back to wherever you came from on this site, else the Board.
      if (document.referrer && new URL(document.referrer).origin === location.origin) history.back();
      else location.href = 'board.html';
    });
  }
  renderAccount(document.getElementById('topbar-account'));
}

function setTopbarTitle(text) {
  const el = document.getElementById('topbar-title');
  if (el) el.textContent = text;
}

let accountMenuBound = false;

function setAccountMenuOpen(open) {
  const menu = document.querySelector('#topbar-account .acct-menu');
  const btn = document.querySelector('#topbar-account .acct-btn');
  if (!menu || !btn) return;
  menu.hidden = !open;
  btn.classList.toggle('open', open);
  btn.setAttribute('aria-expanded', String(open));
}

// Desktop: avatar + name opening a small menu (Profile settings, Log out).
// Phone: the same two entries under a user block, inside the hamburger
// panel. Both are rendered; CSS shows the one that fits the width.
async function renderAccount(el) {
  const user = await currentUser();
  if (!user) {
    const next = encodeURIComponent(location.pathname.split('/').pop() + location.search);
    el.innerHTML = `<a class="btn-gold btn-sm" href="login.html?next=${next}">Log in</a>`;
    return;
  }
  const avatar = (cls) => userAvatarHtml(user.username, user.avatar_url, cls, { admin: user.is_admin });
  el.innerHTML = `
    <span class="topbar-divider"></span>
    <div class="acct">
      <button class="acct-btn" aria-haspopup="menu" aria-expanded="false">
        ${avatar('topbar-avatar')}
        <span class="topbar-username"></span>
        <svg class="acct-chevron" width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="m3 4.5 3 3 3-3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
      <div class="acct-menu" role="menu" hidden>
        <a href="profile.html" role="menuitem" class="acct-item acct-item-primary">${PERSON_ICON}Profile settings</a>
        <div class="acct-sep"></div>
        <button role="menuitem" class="acct-item acct-logout">${LOGOUT_ICON}Log out</button>
      </div>
    </div>
    <div class="acct-phone">
      <div class="acct-phone-user">
        ${avatar('acct-phone-avatar')}
        <div><div class="acct-phone-name"></div>${user.is_admin ? adminBadgeHtml({ solid: true }) : ''}</div>
      </div>
      <a href="profile.html" class="acct-phone-item acct-item-primary">${PERSON_ICON}Profile settings</a>
      <button class="acct-phone-item acct-logout">${LOGOUT_ICON}Log out</button>
    </div>`;
  el.querySelector('.topbar-username').textContent = user.username;
  el.querySelector('.acct-phone-name').textContent = user.username;

  const btn = el.querySelector('.acct-btn');
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    setAccountMenuOpen(el.querySelector('.acct-menu').hidden);
  });
  if (!accountMenuBound) {
    // Page-wide, bound once (this function re-runs after profile edits).
    accountMenuBound = true;
    document.addEventListener('click', (e) => {
      const menu = document.querySelector('#topbar-account .acct-menu');
      if (menu && !menu.hidden && !menu.contains(e.target)) setAccountMenuOpen(false);
    });
    document.addEventListener('keydown', (e) => {
      const menu = document.querySelector('#topbar-account .acct-menu');
      if (e.key === 'Escape' && menu && !menu.hidden) {
        setAccountMenuOpen(false);
        document.querySelector('#topbar-account .acct-btn').focus();
      }
    });
  }
  el.querySelectorAll('.acct-logout').forEach((b) => b.addEventListener('click', async () => {
    await Api.logout().catch(() => {});
    location.href = location.pathname.endsWith('profile.html') ? 'board.html' : location.href;
  }));
}

function pillButton(label, { href, id, gold = false } = {}) {
  const el = document.createElement(href ? 'a' : 'button');
  el.className = gold ? 'btn-gold' : 'pill-button';
  if (href) el.href = href;
  if (id) el.id = id;
  el.textContent = label;
  return el;
}
