(function () {
  function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute('data-theme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    html.classList.toggle('dark', next === 'dark');
    localStorage.setItem('ichat-theme', next);
    if (window.lucide) {
      window.lucide.createIcons();
    }
  }

  window.toggleTheme = toggleTheme;

  document.addEventListener('DOMContentLoaded', () => {
    const button = document.querySelector('[data-auth-theme-toggle]');
    if (button) {
      button.addEventListener('click', toggleTheme);
    }
  });
})();
