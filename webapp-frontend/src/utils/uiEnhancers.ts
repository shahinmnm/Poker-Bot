/**
 * uiEnhancers.ts
 *
 * Four-color deck CSS helper + Haptic feedback wrappers for Telegram WebApp.
 * - No external deps, safe on desktop and outside Telegram.
 * - Call setFourColorDeck(true/false) to enable/disable 4-color suits.
 * - Use haptics.impact('light'|'medium'|'heavy'), haptics.notification('success'|'warning'|'error'), haptics.selection()
 */

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        HapticFeedback?: {
          impactOccurred: (style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft') => void;
          notificationOccurred: (type: 'error' | 'success' | 'warning') => void;
          selectionChanged: () => void;
        };
      };
    };
  }
}

let styleInjected = false;
let hapticsEnabled = true;

const STYLE_ID = 'four-color-deck-css';

// Classic 4-color: ♠ black, ♥ red, ♦ blue, ♣ green
const fourColorCSS = `
:root {
  --suit-spade: #111111;
  --suit-heart: #ff4d4f;
  --suit-diamond: #2ea7ff;
  --suit-club: #2ecc71;
}

/* Activate only when the body has data-four-color="true" */
body[data-four-color="true"] .suit-s, 
body[data-four-color="true"] .suit-spade,
body[data-four-color="true"] [data-suit="S"] {
  color: var(--suit-spade) !important;
  fill: var(--suit-spade) !important;
}

body[data-four-color="true"] .suit-h, 
body[data-four-color="true"] .suit-heart,
body[data-four-color="true"] [data-suit="H"] {
  color: var(--suit-heart) !important;
  fill: var(--suit-heart) !important;
}

body[data-four-color="true"] .suit-d, 
body[data-four-color="true"] .suit-diamond,
body[data-four-color="true"] [data-suit="D"] {
  color: var(--suit-diamond) !important;
  fill: var(--suit-diamond) !important;
}

body[data-four-color="true"] .suit-c, 
body[data-four-color="true"] .suit-club,
body[data-four-color="true"] [data-suit="C"] {
  color: var(--suit-club) !important;
  fill: var(--suit-club) !important;
}

/* Optional helpers you can add to any markup */
.suit-s,.suit-spade,[data-suit="S"] { color: currentColor; }
.suit-h,.suit-heart,[data-suit="H"] { color: currentColor; }
.suit-d,.suit-diamond,[data-suit="D"] { color: currentColor; }
.suit-c,.suit-club,[data-suit="C"] { color: currentColor; }

/* Example card face helpers (use if you render ranks/suits inline) */
body[data-four-color="true"] .card-face .rank { font-weight: 800; }
body[data-four-color="true"] .card-face .suit { opacity: 0.95; }
`;

/** Ensure our CSS is present once */
function ensureStyleInjected() {
  if (typeof document === 'undefined' || styleInjected) return;
  const el = document.createElement('style');
  el.id = STYLE_ID;
  el.appendChild(document.createTextNode(fourColorCSS));
  document.head.appendChild(el);
  styleInjected = true;
}

/** Enable/disable four-color deck globally by toggling a data-attr on <body> */
export function setFourColorDeck(enabled: boolean) {
  if (typeof document === 'undefined') return;
  ensureStyleInjected();
  if (enabled) {
    document.body.setAttribute('data-four-color', 'true');
  } else {
    document.body.removeAttribute('data-four-color');
  }
}

/** Programmatic check */
export function isFourColorDeck(): boolean {
  if (typeof document === 'undefined') return false;
  return document.body.getAttribute('data-four-color') === 'true';
}

/** Allow UI to toggle haptics (default on) */
export function setHapticsEnabled(enabled: boolean) {
  hapticsEnabled = !!enabled;
}

/** Safe Telegram HapticFeedback access */
function tgHaptic() {
  return window?.Telegram?.WebApp?.HapticFeedback;
}

/** Fallback vibration helper */
function vibrate(ms: number | number[]) {
  try {
    if ('vibrate' in navigator) {
      // @ts-ignore
      navigator.vibrate(ms);
    }
  } catch {
    /* noop */
  }
}

/** Haptic wrappers with graceful fallback */
export const haptics = {
  impact(level: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft' = 'light') {
    if (!hapticsEnabled) return;
    const h = tgHaptic();
    if (h?.impactOccurred) {
      h.impactOccurred(level);
    } else {
      // fallback suggestion: light 10ms, medium 20ms, heavy 30ms
      const ms = level === 'heavy' ? 30 : level === 'medium' ? 20 : 10;
      vibrate(ms);
    }
  },

  notification(type: 'success' | 'warning' | 'error' = 'success') {
    if (!hapticsEnabled) return;
    const h = tgHaptic();
    if (h?.notificationOccurred) {
      h.notificationOccurred(type);
    } else {
      // fallback pattern
      const pattern = type === 'success' ? [12, 30, 12] : type === 'warning' ? [25, 40, 25] : [40, 40, 40];
      vibrate(pattern);
    }
  },

  selection() {
    if (!hapticsEnabled) return;
    const h = tgHaptic();
    if (h?.selectionChanged) {
      h.selectionChanged();
    } else {
      vibrate(8);
    }
  },

  /** Convenience shortcut for button presses */
  click() {
    haptics.impact('light');
  },
};

/** One-shot helper to apply both UX settings at once (nice to call after loading settings) */
export function applyUXSettings(opts: { fourColorDeck?: boolean; haptics?: boolean } = {}) {
  if (typeof opts.fourColorDeck === 'boolean') {
    setFourColorDeck(opts.fourColorDeck);
  }
  if (typeof opts.haptics === 'boolean') {
    setHapticsEnabled(opts.haptics);
  }
}
