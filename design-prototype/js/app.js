/* Cogni Board — Design Prototype Interactions */

(function () {
  'use strict';

  // ── Dark Mode Toggle ──
  function getPreferredTheme() {
    const saved = localStorage.getItem('cogni-theme');
    if (saved) return saved;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('cogni-theme', theme);
    const sun = document.querySelector('.sun-icon');
    const moon = document.querySelector('.moon-icon');
    if (sun && moon) {
      sun.style.display = theme === 'dark' ? 'none' : 'block';
      moon.style.display = theme === 'dark' ? 'block' : 'none';
    }
  }

  window.toggleTheme = function () {
    const current = document.documentElement.getAttribute('data-theme');
    applyTheme(current === 'dark' ? 'light' : 'dark');
  };

  applyTheme(getPreferredTheme());

  // ── Password Visibility Toggle ──
  document.querySelectorAll('.input-group .btn-icon').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const input = this.closest('.input-group').querySelector('input');
      if (!input) return;
      const isPassword = input.type === 'password';
      input.type = isPassword ? 'text' : 'password';
      this.innerHTML = isPassword
        ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
        : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
    });
  });

  // ── Chip Toggle (Signup Steps) ──
  document.querySelectorAll('.chip-group').forEach(function (group) {
    group.addEventListener('click', function (e) {
      const chip = e.target.closest('.chip');
      if (!chip) return;
      if (group.hasAttribute('data-multi')) {
        chip.classList.toggle('active');
      } else {
        group.querySelectorAll('.chip').forEach(function (c) { c.classList.remove('active'); });
        chip.classList.add('active');
      }
    });
  });

  // ── Signup Step Navigation ──
  const steps = document.querySelectorAll('.step');
  let currentStep = 1;

  window.goToStep = function (n) {
    if (n < 1 || n > steps.length) return;
    currentStep = n;
    steps.forEach(function (step, i) {
      step.classList.toggle('active', i < n);
      step.classList.toggle('done', i < n);
    });
    // Could also show/hide step content panels here
  };

  // ── Builder Tabs (Graphs / Chat) ──
  document.querySelectorAll('.builder-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      this.closest('.builder-tabs').querySelectorAll('.builder-tab').forEach(function (t) {
        t.classList.remove('active');
      });
      this.classList.add('active');
    });
  });

  // ── Canvas Tabs ──
  document.querySelectorAll('.canvas-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      this.closest('.canvas-tabs').querySelectorAll('.canvas-tab').forEach(function (t) {
        t.classList.remove('active');
      });
      this.classList.add('active');
    });
  });

  // ── Sidebar Nav ──
  document.querySelectorAll('.sidebar-nav').forEach(function (nav) {
    nav.addEventListener('click', function (e) {
      const item = e.target.closest('.sidebar-nav-item');
      if (!item) return;
      nav.querySelectorAll('.sidebar-nav-item').forEach(function (i) { i.classList.remove('active'); });
      item.classList.add('active');
    });
  });

  // ── Social Button Hover Effect ──
  document.querySelectorAll('.social-btn').forEach(function (btn) {
    btn.addEventListener('mouseenter', function () {
      this.style.background = 'var(--bg-tertiary)';
    });
    btn.addEventListener('mouseleave', function () {
      this.style.background = 'var(--bg-secondary)';
    });
  });

})();
