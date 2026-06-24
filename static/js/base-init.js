(function () {
  const savedTheme = localStorage.getItem('ichat-theme') || 'light';
  document.documentElement.setAttribute('data-theme', savedTheme);
  if (savedTheme === 'dark') {
    document.body.classList.add('dark');
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (window.lucide) {
      window.lucide.createIcons();
    }
    setTimeout(() => {
      document.body.classList.remove('preload');
    }, 100);
  });
})();
