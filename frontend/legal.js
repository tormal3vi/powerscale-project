// Terms of Service and Privacy Policy: static text, plus the Discord invite.
renderTopbar([]);

const invite = discordInvite();
document.querySelectorAll('.faq-discord').forEach((el) => {
  if (!invite) return;
  const a = document.createElement('a');
  a.href = invite;
  a.target = '_blank';
  a.rel = 'noopener';
  a.textContent = el.textContent;
  el.replaceWith(a);
});
