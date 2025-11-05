import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

/**
 * main.tsx
 * Minimal React 18 bootstrap for the Telegram Poker mini-app.
 *
 * Goals
 *  - Keep the mini-app size EXACT (fill parent only; no viewport hacks)
 *  - Respect Telegram theme variables with sensible fallbacks
 *  - No external CSS files required (single-file drop-in)
 */

// Inject minimal global styles once (no layout shifts, full-height app)
(function injectGlobalStyles() {
  const css = `
    :root {
      /* Fallbacks when running locally outside TG */
      --tg-theme-bg-color: #0f0f0f;
      --tg-theme-text-color: #ffffff;
      --tg-theme-secondary-bg-color: rgba(255,255,255,0.04);
    }
    html, body, #root {
      height: 100%;
      width: 100%;
      margin: 0;
      padding: 0;
      background: var(--tg-theme-bg-color);
      color: var(--tg-theme-text-color);
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, Cantarell, "Noto Sans",
                   Arial, "Helvetica Neue", "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol",
                   sans-serif;
    }
    button, input, select {
      font: inherit;
      color: inherit;
    }
    /* Avoid accidental overscroll bounce inside the mini-app container */
    body { overscroll-behavior: contain; }
  `;
  const style = document.createElement('style');
  style.setAttribute('data-injected', 'global-telegram-safe');
  style.appendChild(document.createTextNode(css));
  document.head.appendChild(style);
})();

// Optional: listen to Telegram theme changes to keep fallbacks in sync when testing outside TG
(function bridgeTelegramTheme() {
  const tg = (window as any)?.Telegram?.WebApp;
  if (!tg) return;

  // On theme change, Telegram updates CSS variables itself.
  // We don't need to mirror them, but we can force a repaint if needed:
  const onThemeChanged = () => {
    // Trigger a lightweight repaint by toggling a data attribute.
    document.documentElement.setAttribute(
      'data-tg-color-scheme',
      tg.colorScheme === 'dark' ? 'dark' : 'light'
    );
  };

  try {
    tg.onEvent?.('themeChanged', onThemeChanged);
    // Initial set
    onThemeChanged();
    // Cleanup on HMR/full reload handled by page lifecycle
  } catch {
    // Safe no-op if Telegram API not available
  }
})();

// Mount React app
const container = document.getElementById('root');
if (!container) {
  const rootDiv = document.createElement('div');
  rootDiv.id = 'root';
  document.body.appendChild(rootDiv);
}

createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
