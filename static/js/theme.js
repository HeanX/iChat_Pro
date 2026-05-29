/**
 * theme.js - Light/Dark Mode Manager for iChat Pro
 * 
 * Manages theme state, saves user preferences in localStorage,
 * applies the theme to the HTML element via the `data-theme` attribute,
 * and sets up toggle event listeners.
 */

(function () {
    const THEME_STORAGE_KEY = 'ichat-theme';
    const THEMES = {
        LIGHT: 'light',
        DARK: 'dark'
    };

    /**
     * Determines the initial theme.
     * Checks localStorage, falling back to OS preference, then default (light).
     */
    function getPreferredTheme() {
        const savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
        if (savedTheme === THEMES.LIGHT || savedTheme === THEMES.DARK) {
            return savedTheme;
        }
        // Fallback to system preference
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            return THEMES.DARK;
        }
        return THEMES.LIGHT;
    }

    /**
     * Applies the theme to the HTML element.
     * Sets document attribute, updates localStorage, and notifies listeners.
     */
    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem(THEME_STORAGE_KEY, theme);

        // Update UI controls if they exist (useful for checkboxes/switches)
        const toggles = document.querySelectorAll('.theme-toggle, #theme-toggle');
        toggles.forEach(toggle => {
            if (toggle.type === 'checkbox') {
                toggle.checked = (theme === THEMES.DARK);
            }
        });

        // Dispatch custom event so other components (e.g. Chat app) can react if needed
        const event = new CustomEvent('themeChanged', { detail: { theme } });
        window.dispatchEvent(event);
    }

    /**
     * Toggles between dark and light themes.
     */
    function toggleTheme() {
        const currentTheme = document.documentElement.getAttribute('data-theme') || THEMES.LIGHT;
        const newTheme = currentTheme === THEMES.DARK ? THEMES.LIGHT : THEMES.DARK;
        applyTheme(newTheme);
        return newTheme;
    }

    // Apply the preferred theme immediately during script parse to avoid FOUC (Flash of Unstyled Content)
    const initialTheme = getPreferredTheme();
    applyTheme(initialTheme);

    // Bind DOM events on load
    document.addEventListener('DOMContentLoaded', () => {
        // Initial binding to any theme toggles in the page
        const setupThemeListeners = () => {
            const toggles = document.querySelectorAll('.theme-toggle, #theme-toggle');
            toggles.forEach(toggle => {
                // Ensure correct initial visual state
                if (toggle.type === 'checkbox') {
                    toggle.checked = (getPreferredTheme() === THEMES.DARK);
                }

                // Remove existing listener to prevent duplicate binding if function runs again
                toggle.removeEventListener('click', handleToggleClick);
                toggle.addEventListener('click', handleToggleClick);
            });
        };

        function handleToggleClick(e) {
            const toggle = e.currentTarget;
            if (toggle.type === 'checkbox') {
                applyTheme(toggle.checked ? THEMES.DARK : THEMES.LIGHT);
            } else {
                toggleTheme();
            }
        }

        // Initialize listeners
        setupThemeListeners();

        // Listen for system appearance changes
        if (window.matchMedia) {
            window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
                // Only follow system changes if the user has not set a preference in localStorage
                if (!localStorage.getItem(THEME_STORAGE_KEY)) {
                    applyTheme(e.matches ? THEMES.DARK : THEMES.LIGHT);
                }
            });
        }

        // Set up mutation observer to bind new theme toggles if HTML changes dynamically (e.g. settings page swap)
        const observer = new MutationObserver(() => {
            setupThemeListeners();
        });
        observer.observe(document.body, { childList: true, subtree: true });
    });

    // Expose ThemeManager globally
    window.ThemeManager = {
        getTheme: () => document.documentElement.getAttribute('data-theme') || THEMES.LIGHT,
        setTheme: applyTheme,
        toggle: toggleTheme,
        THEMES: THEMES
    };
})();
