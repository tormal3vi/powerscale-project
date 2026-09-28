// Shared top bar for every page. Desktop: brand, section links, page
// actions, then the account area behind a thin divider. Phone: logo mark,
// the page's title and a menu button that opens the links, actions and
// account as a panel under the bar.

const NAV_LINKS = [
  { href: 'browse.html', label: 'Characters' },
  { href: 'tournament.html', label: 'Tournament' },
  { href: 'duels.html', label: 'Duels' },
  { href: 'board.html', label: 'Board' },
];
const PAGE_TITLES = {
  'browse.html': 'Characters', 'compare.html': 'Compare', 'character.html': 'Character',
  'tournament.html': 'Tournament', 'board.html': 'Board', 'login.html': 'Log in',
  'profile.html': 'Profile settings', 'duels.html': 'Duels', 'tickets.html': 'Tickets', 'user.html': 'Profile',
};

const MENU_ICON = '<svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M3 6h14M3 10h14M3 14h14" stroke="#D8D0C0" stroke-width="1.4" stroke-linecap="round"/></svg>';
const CLOSE_ICON = '<svg width="18" height="18" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="#D8D0C0" stroke-width="1.5" stroke-linecap="round"/></svg>';
const PERSON_ICON = '<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="5.5" r="2.5" stroke="#D8D0C0" stroke-width="1.3"/><path d="M3 13c0-2.5 2.2-4 5-4s5 1.5 5 4" stroke="#D8D0C0" stroke-width="1.3" stroke-linecap="round"/></svg>';
const LOGOUT_ICON = '<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M6 3H3v10h3M10 5l3 3-3 3M13 8H6" stroke="#A69C8C" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// `back`: on phones, a back arrow replaces the logo and menu (Profile
// settings is a place you step into and out of).
// The Discord logo (Simple Icons, CC0).
const DISCORD_ICON = '<svg class="discord-icon" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M20.317 4.37a19.79 19.79 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.865-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.74 19.74 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.1 13.1 0 0 1-1.872-.892.077.077 0 0 1-.008-.128c.126-.094.252-.192.372-.291a.074.074 0 0 1 .078-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.009c.12.099.246.198.373.292a.077.077 0 0 1-.006.127 12.3 12.3 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.84 19.84 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03ZM8.02 15.331c-1.182 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418Zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418Z"/></svg>';

// The server's invite link, when the site has one (the server writes it
// into the page: DISCORD_INVITE_URL).
function discordInvite() {
  const url = document.querySelector('meta[name="discord-invite"]')?.content || '';
  return /^https:\/\/(discord\.gg|discord\.com\/invite)\/[A-Za-z0-9-]+$/.test(url) ? url : '';
}

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
      ${discordInvite() ? `<a class="topbar-discord" href="${escapeHtml(discordInvite())}" target="_blank" rel="noopener"
        aria-label="Join our Discord" title="Join our Discord">${DISCORD_ICON}<span class="topbar-discord-label">Join our Discord</span></a>` : ''}
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
        <a href="user.html?u=${encodeURIComponent(user.username)}" role="menuitem" class="acct-item acct-item-primary">${PERSON_ICON}Your profile</a>
        <a href="profile.html" role="menuitem" class="acct-item">${PERSON_ICON}Profile settings</a>
        ${user.is_admin ? `<a href="tickets.html" role="menuitem" class="acct-item acct-tickets">${PERSON_ICON}Tickets <span class="acct-count"></span></a>` : ''}
        <div class="acct-sep"></div>
        <button role="menuitem" class="acct-item acct-logout">${LOGOUT_ICON}Log out</button>
      </div>
    </div>
    <div class="acct-phone">
      <div class="acct-phone-user">
        ${avatar('acct-phone-avatar')}
        <div><div class="acct-phone-name"></div>${user.is_admin ? adminBadgeHtml({ solid: true }) : ''}</div>
      </div>
      <a href="user.html?u=${encodeURIComponent(user.username)}" class="acct-phone-item acct-item-primary">${PERSON_ICON}Your profile</a>
      <a href="profile.html" class="acct-phone-item">${PERSON_ICON}Profile settings</a>
      ${user.is_admin ? `<a href="tickets.html" class="acct-phone-item acct-tickets">${PERSON_ICON}Tickets <span class="acct-count"></span></a>` : ''}
      <button class="acct-phone-item acct-logout">${LOGOUT_ICON}Log out</button>
    </div>`;
  el.querySelector('.topbar-username').textContent = user.username;
  el.querySelector('.acct-phone-name').textContent = user.username;
  showDuelsDot();
  if (user.is_admin) {
    Api.adminTicketCount().then(({ open }) => {
      el.querySelectorAll('.acct-count').forEach((c) => { c.textContent = open ? String(open) : ''; });
      if (open) el.querySelector('.acct-btn')?.classList.add('has-dot');
    }).catch(() => {});
  }

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

// A gold dot on Duels (and, on phones, the menu button) while a challenge
// is waiting for you or you have rounds to play. Fetched after the bar is
// drawn, so it never holds the page up. The Duels page passes the count
// from the games it already loaded instead.
async function showDuelsDot(count) {
  if (count === undefined) {
    if (location.pathname.endsWith('duels.html')) return; // duels.js sets it
    try { count = (await Api.pendingGames()).count; } catch { return; }
  }
  const link = document.querySelector('.topbar-nav a[href="duels.html"]');
  if (!link) return;
  link.classList.toggle('has-dot', count > 0);
  link.title = count ? `${count} duel${count === 1 ? '' : 's'} waiting for you` : '';
  document.querySelector('.topbar-menu-btn')?.classList.toggle('has-dot', count > 0);
}

// A small menu under a button: copy the link, post it to Reddit or X, or
// the phone's own share sheet - plus whatever the page adds (extras:
// [{ label, onClick, quiet }], quiet ones set apart at the bottom).
// getShare() returns { url, title, text } when the menu opens.
function attachShareMenu(button, getShare, extras = []) {
  const wrap = document.createElement('span');
  wrap.className = 'share-wrap';
  button.replaceWith(wrap);
  wrap.appendChild(button);
  button.setAttribute('aria-haspopup', 'menu');
  button.setAttribute('aria-expanded', 'false');
  const menu = document.createElement('div');
  menu.className = 'share-menu';
  menu.setAttribute('role', 'menu');
  menu.hidden = true;
  wrap.appendChild(menu);
  const close = () => { menu.hidden = true; button.setAttribute('aria-expanded', 'false'); };
  const item = (label, onClick, cls = '') => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `share-item ${cls}`;
    b.setAttribute('role', 'menuitem');
    b.textContent = label;
    b.addEventListener('click', () => onClick(b));
    menu.appendChild(b);
    return b;
  };
  const open = () => {
    const share = getShare();
    if (!share) return;
    const enc = encodeURIComponent;
    menu.innerHTML = '';
    item('Copy link', async (b) => {
      try { await navigator.clipboard.writeText(share.url); b.textContent = 'Link copied'; } catch { b.textContent = 'Copy failed'; }
      setTimeout(close, 900);
    });
    item('Share on Reddit', () => { window.open(`https://www.reddit.com/submit?url=${enc(share.url)}&title=${enc(share.title)}`, '_blank', 'noopener'); close(); });
    item('Share on X', () => { window.open(`https://x.com/intent/tweet?text=${enc(share.text || share.title)}&url=${enc(share.url)}`, '_blank', 'noopener'); close(); });
    if (navigator.share) {
      item('More…', async () => {
        close();
        try { await navigator.share({ title: share.title, text: share.text || share.title, url: share.url }); } catch { /* dismissed */ }
      });
    }
    const loud = extras.filter((x) => !x.quiet);
    const quiet = extras.filter((x) => x.quiet);
    loud.forEach((x) => item(x.label, (b) => x.onClick(b, close), 'share-extra'));
    if (quiet.length) {
      const hr = document.createElement('div');
      hr.className = 'share-sep';
      menu.appendChild(hr);
      quiet.forEach((x) => item(x.label, (b) => x.onClick(b, close), 'share-quiet'));
    }
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
  };
  button.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.hidden) open(); else close();
  });
  document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  return wrap;
}

function pillButton(label, { href, id, gold = false } = {}) {
  const el = document.createElement(href ? 'a' : 'button');
  el.className = gold ? 'btn-gold' : 'pill-button';
  if (href) el.href = href;
  if (id) el.id = id;
  el.textContent = label;
  return el;
}
