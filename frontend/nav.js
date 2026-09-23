// Shared top bar for every page: logo, section links, and a slot on the
// right for page-specific actions (passed in as DOM nodes).

const NAV_LINKS = [
  { href: 'browse.html', label: 'Characters' },
  { href: 'tournament.html', label: 'Tournament' },
];

function renderTopbar(actions = []) {
  const bar = document.getElementById('topbar');
  const current = location.pathname.split('/').pop() || 'browse.html';
  bar.className = 'topbar';
  bar.innerHTML = `
    <div class="topbar-left">
      <a class="topbar-brand" href="browse.html">
        <span class="topbar-mark"></span>
        <span class="topbar-logo">Powerscale</span>
      </a>
      <nav class="topbar-nav">
        ${NAV_LINKS.map((l) => `<a href="${l.href}" class="${l.href === current ? 'active' : ''}">${l.label}</a>`).join('')}
      </nav>
    </div>
    <div class="topbar-left topbar-actions" id="topbar-actions"></div>
  `;
  const slot = document.getElementById('topbar-actions');
  for (const node of actions) slot.appendChild(node);
}

function pillButton(label, { href, id } = {}) {
  const el = document.createElement(href ? 'a' : 'button');
  el.className = 'pill-button';
  if (href) el.href = href;
  if (id) el.id = id;
  el.textContent = label;
  return el;
}
