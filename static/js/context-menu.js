/**
 * iChat Pro — Reusable Context Menu Component
 *
 * Singleton right-click / long-press context menu.
 * Shared by conversation sidebar items (T12) and message bubbles (T13).
 *
 * Usage:
 *   ContextMenu.show(x, y, [
 *     { icon: 'pin', label: 'Pin', onClick: () => { ... } },
 *     { icon: 'trash-2', label: 'Delete', danger: true, onClick: () => { ... } },
 *     { divider: true },
 *     { icon: 'check', label: 'Mark as Read', onClick: () => { ... } },
 *   ]);
 *   ContextMenu.hide();
 */

(function () {
  'use strict';

  let backdropEl = null;
  let menuEl = null;
  let visible = false;

  function ensureDOM() {
    if (backdropEl && menuEl) return;

    // Backdrop — transparent full‑screen overlay to capture outside clicks
    backdropEl = document.createElement('div');
    backdropEl.className = 'context-menu-backdrop';
    backdropEl.addEventListener('click', hide);
    backdropEl.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      hide();
    });
    document.body.appendChild(backdropEl);

    // Menu container
    menuEl = document.createElement('div');
    menuEl.className = 'context-menu';
    menuEl.setAttribute('role', 'menu');
    menuEl.setAttribute('tabindex', '-1');
    document.body.appendChild(menuEl);

    // Global keyboard handler
    document.addEventListener('keydown', onKeyDown);
  }

  function onKeyDown(e) {
    if (!visible) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      hide();
    }
    // Arrow-key navigation
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const items = menuEl.querySelectorAll('.context-menu-item:not(.context-menu-divider)');
      if (items.length === 0) return;
      const focused = menuEl.querySelector('.context-menu-item.focused');
      let idx = -1;
      if (focused) {
        focused.classList.remove('focused');
        idx = Array.from(items).indexOf(focused);
      }
      if (e.key === 'ArrowDown') idx = (idx + 1) % items.length;
      else idx = (idx - 1 + items.length) % items.length;
      items[idx].classList.add('focused');
      items[idx].focus();
    }
    if (e.key === 'Enter' && visible) {
      e.preventDefault();
      const focused = menuEl.querySelector('.context-menu-item.focused');
      if (focused) focused.click();
    }
  }

  function hide() {
    if (!visible) return;
    visible = false;
    backdropEl.style.display = 'none';
    menuEl.classList.remove('context-menu-visible');
    menuEl.innerHTML = '';
  }

  /**
   * Show the context menu at the given viewport coordinates.
   *
   * @param {number} x  — clientX (left edge of menu)
   * @param {number} y  — clientY (top edge of menu)
   * @param {Array}  items
   *   Each item: { icon?, label, danger?, onClick, divider?: true }
   *   A divider-only entry: { divider: true }
   */
  function show(x, y, items) {
    if (!items || items.length === 0) return;
    ensureDOM();

    // Build menu HTML
    let html = '';
    items.forEach(function (item) {
      if (item.divider) {
        html += '<div class="context-menu-divider" role="separator"></div>';
        return;
      }
      var dangerClass = item.danger ? ' danger' : '';
      var iconHtml = item.icon
        ? '<i data-lucide="' + escapeAttr(item.icon) + '" class="context-menu-item-icon"></i>'
        : '<span class="context-menu-item-icon"></span>'; // placeholder for alignment
      html +=
        '<button class="context-menu-item' + dangerClass + '" role="menuitem" tabindex="-1">' +
        iconHtml +
        '<span class="context-menu-item-label">' + escapeHtml(item.label) + '</span>' +
        '</button>';
    });

    menuEl.innerHTML = html;

    // Bind click handlers
    var buttons = menuEl.querySelectorAll('.context-menu-item');
    items.forEach(function (item, idx) {
      if (item.divider) return;
      var btn = buttons[Array.from(menuEl.querySelectorAll('.context-menu-item')).indexOf(buttons[idx > 0 ? idx : 0])];
      // More reliable: map filtered items to buttons
    });

    // Re-bind cleanly
    var filteredItems = items.filter(function (it) { return !it.divider; });
    var allButtons = menuEl.querySelectorAll('.context-menu-item');
    allButtons.forEach(function (btn, i) {
      var item = filteredItems[i];
      if (!item) return;
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        hide();
        if (typeof item.onClick === 'function') item.onClick();
      });
      btn.addEventListener('mouseenter', function () {
        allButtons.forEach(function (b) { b.classList.remove('focused'); });
        btn.classList.add('focused');
      });
    });

    // Render icons
    if (window.lucide && window.lucide.createIcons) {
      window.lucide.createIcons({ nodes: menuEl.querySelectorAll('[data-lucide]') });
    }

    // Show
    visible = true;
    backdropEl.style.display = 'block';

    // Position: first render off‑screen to measure
    menuEl.style.visibility = 'hidden';
    menuEl.style.left = '0px';
    menuEl.style.top = '0px';
    menuEl.classList.add('context-menu-visible');

    // Measure and reposition
    var menuW = menuEl.offsetWidth;
    var menuH = menuEl.offsetHeight;
    var vw = window.innerWidth;
    var vh = window.innerHeight;

    var left = x;
    var top = y;

    // Flip horizontally if it would overflow right edge
    if (left + menuW > vw - 8) {
      left = x - menuW;
    }
    if (left < 8) left = 8;

    // Flip vertically if it would overflow bottom edge
    if (top + menuH > vh - 8) {
      top = y - menuH;
    }
    if (top < 8) top = 8;

    menuEl.style.left = left + 'px';
    menuEl.style.top = top + 'px';
    menuEl.style.visibility = 'visible';

    // Focus first item for keyboard nav
    if (allButtons.length > 0) {
      allButtons[0].classList.add('focused');
    }
  }

  // --- Helpers (self‑contained, no external deps) ---

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function escapeAttr(str) {
    return String(str || '').replace(/"/g, '&quot;');
  }

  // --- Global scroll / resize cleanup ---
  window.addEventListener('scroll', function () {
    if (visible) hide();
  }, true); // capture phase to catch message-container scroll

  window.addEventListener('resize', function () {
    if (visible) hide();
  });

  // --- Public API ---
  window.ContextMenu = {
    show: show,
    hide: hide,
  };
})();
