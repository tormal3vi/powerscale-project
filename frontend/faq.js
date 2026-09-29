// The FAQ: static answers; this fills in the live roster size and the
// Discord links, and opens the question a link points at (faq.html#draft).

renderTopbar([]);

(async () => {
  try {
    const total = (await Api.listCategories()).reduce((n, c) => n + c.count, 0);
    document.querySelectorAll('.faq-count').forEach((el) => { el.textContent = total.toLocaleString('en'); });
  } catch { /* "thousands of" stays */ }
})();

const invite = discordInvite();
document.querySelectorAll('.faq-discord').forEach((el) => {
  if (!invite) return;  // plain text until the site has an invite link
  const a = document.createElement('a');
  a.href = invite;
  a.target = '_blank';
  a.rel = 'noopener';
  a.textContent = el.textContent;
  el.replaceWith(a);
});

function openFromHash() {
  const target = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (!target) return;
  const item = target.closest('.faq-item') || (target.classList.contains('faq-item') ? target : null);
  if (item) {
    item.open = true;
    item.scrollIntoView({ block: 'start' });
  }
}
openFromHash();
window.addEventListener('hashchange', openFromHash);

// Opening a question puts its link in the address bar, to share it.
document.querySelectorAll('.faq-item').forEach((item) => item.addEventListener('toggle', () => {
  if (item.open) history.replaceState(null, '', `#${item.id}`);
}));
