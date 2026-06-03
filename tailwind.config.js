/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './templates/**/*.html',
    './static/js/**/*.js',
  ],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        primary: 'var(--color-primary)',
        bgMain: 'var(--color-bg)',
        textMain: 'var(--color-text-main)',
        textSecondary: 'var(--color-text-secondary)',
        borderColor: 'var(--color-border)',
        bgSidebar: 'var(--color-sidebar-bg)',
        bgSearch: 'var(--color-search-bg)',
        brand: {
          light: '#3390ec',
          dark: '#8774e1',
        },
      },
      borderRadius: {
        'custom-sm': 'var(--radius-sm)',
        'custom-md': 'var(--radius-md)',
        'custom-lg': 'var(--radius-lg)',
      },
    },
  },
};
