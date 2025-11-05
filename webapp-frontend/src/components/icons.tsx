// webapp-frontend/src/components/icons.tsx
//
// Lightweight icon set for the Poker WebApp.
// - All icons are 24x24, stroke-based, using currentColor.
// - Export names match component imports elsewhere.

import * as React from "react";

export type IconProps = React.SVGProps<SVGSVGElement>;

function BaseSvg(props: IconProps) {
  const { children, ...rest } = props;
  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

/* --- Core set used across the app --- */

export const BadgeCheckIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M12 3l2.5 1.5L17 3l1 2.8 2.8 1L20.5 9 21 12l-2.5 1.7.3 3.2-3-1.1L12 18l-3.8-2.2-3 1.1.3-3.2L3 12l.5-3.1L2.2 6.8 5 5.8 6 3l2.5 1.5L12 3Z" />
    <path d="M8.5 12l2.5 2.5L16 9.5" />
  </BaseSvg>
);

export const BellIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M6 10a6 6 0 1 1 12 0c0 3 1.5 4.5 1.5 4.5H4.5S6 13 6 10Z" />
    <path d="M9 18a3 3 0 0 0 6 0" />
  </BaseSvg>
);

export const ChartIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M3 3v18h18" />
    <path d="M7 13l4 4 6-8" />
  </BaseSvg>
);

export const CogIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a7.8 7.8 0 0 0 .1-2l2-1.2-2-3.6-2.3.6a7.8 7.8 0 0 0-1.7-1l-.3-2.4H11l-.3 2.4a7.8 7.8 0 0 0-1.7 1l-2.3-.6-2 3.6 2 1.2a7.8 7.8 0 0 0 .1 2l-2 1.2 2 3.6 2.3-.6c.5.4 1.1.7 1.7 1l.3 2.4h4.1l.3-2.4c.6-.3 1.2-.6 1.7-1l2.3.6 2-3.6-2-1.2Z" />
  </BaseSvg>
);

export const FlameIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M12 3s2 2 2 5-2 4-2 4 6-1 6 5a6 6 0 0 1-12 0c0-5 6-6 6-14Z" />
  </BaseSvg>
);

export const HandIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M8 12V7a2 2 0 1 1 4 0v5" />
    <path d="M12 12V6a2 2 0 1 1 4 0v6" />
    <path d="M16 12V8a2 2 0 1 1 4 0v5c0 3-2 6-6 6H9a5 5 0 0 1-5-5v-2a2 2 0 1 1 4 0v1" />
  </BaseSvg>
);

export const LayoutIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <path d="M3 9h18M9 21V9" />
  </BaseSvg>
);

export const MoonIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z" />
  </BaseSvg>
);

export const PercentIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M19 5L5 19" />
    <circle cx="7" cy="7" r="3" />
    <circle cx="17" cy="17" r="3" />
  </BaseSvg>
);

export const SunIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l-1.5-1.5M20.5 20.5 19 19M5 19l-1.5 1.5M20.5 3.5 19 5" />
  </BaseSvg>
);

export const TrophyIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M8 21h8" />
    <path d="M12 17a5 5 0 0 0 5-5V4H7v8a5 5 0 0 0 5 5Z" />
    <path d="M7 6H4a3 3 0 0 0 3 3M17 6h3a3 3 0 0 1-3 3" />
  </BaseSvg>
);

export const UserIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 20a8 8 0 0 1 16 0" />
  </BaseSvg>
);

/* --- Utility & poker-themed extras --- */

export const CardsIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <rect x="3" y="4" width="8" height="12" rx="2" />
    <rect x="11" y="8" width="10" height="12" rx="2" />
  </BaseSvg>
);

export const CoinsIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <ellipse cx="12" cy="5" rx="7" ry="3" />
    <path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5" />
    <path d="M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
  </BaseSvg>
);

export const TrendingUpIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M3 17l6-6 4 4 7-7" />
  </BaseSvg>
);

export const TrendingDownIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M3 7l6 6 4-4 7 7" />
  </BaseSvg>
);

export const LockIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <rect x="4" y="11" width="16" height="9" rx="2" />
    <path d="M8 11V8a4 4 0 1 1 8 0v3" />
  </BaseSvg>
);

export const UsersIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M16 21a6 6 0 0 0-12 0" />
    <circle cx="10" cy="8" r="4" />
    <path d="M22 21a5 5 0 0 0-6-4.8" />
    <path d="M20 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
  </BaseSvg>
);

export const PlayIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M8 5v14l11-7-11-7Z" />
  </BaseSvg>
);

export const ArrowRightIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M5 12h14" />
    <path d="M13 5l7 7-7 7" />
  </BaseSvg>
);

/* --- Extras used by settings panel --- */

export const PlusIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M12 5v14M5 12h14" />
  </BaseSvg>
);

export const ShieldIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <path d="M12 3l7 4v6a7 7 0 0 1-7 7 7 7 0 0 1-7-7V7l7-4Z" />
  </BaseSvg>
);

export const WalletIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <rect x="3" y="7" width="18" height="12" rx="2" />
    <path d="M21 10h-6a2 2 0 0 0 0 4h6v-4Z" />
    <circle cx="15.5" cy="12" r="0.5" />
  </BaseSvg>
);

export const GlobeIcon = (props: IconProps) => (
  <BaseSvg {...props}>
    <circle cx="12" cy="12" r="10" />
    <path d="M2 12h20" />
    <path d="M12 2a15 15 0 0 0 0 20a15 15 0 0 0 0-20Z" />
  </BaseSvg>
);

/* --- Alias --- */
export const SettingsIcon = CogIcon;
