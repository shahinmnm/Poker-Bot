import React from "react";

export const IconCards = (props: React.SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true" {...props}>
    <rect x="3" y="4" width="7" height="12" rx="2" />
    <rect x="14" y="8" width="7" height="12" rx="2" />
  </svg>
);

export const IconTrophy = (props: React.SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true" {...props}>
    <path d="M8 21h8M12 17a5 5 0 0 0 5-5V4H7v8a5 5 0 0 0 5 5Z" fill="none" stroke="currentColor" strokeWidth="2"/>
    <path d="M7 6H4a3 3 0 0 0 3 3M17 6h3a3 3 0 0 1-3 3" fill="none" stroke="currentColor" strokeWidth="2"/>
  </svg>
);

export const IconTrending = (props: React.SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true" {...props}>
    <path d="M3 17l6-6 4 4 7-7" fill="none" stroke="currentColor" strokeWidth="2"/>
    <path d="M14 8h7v7" fill="none" stroke="currentColor" strokeWidth="2"/>
  </svg>
);

export const IconCoins = (props: React.SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true" {...props}>
    <ellipse cx="12" cy="5" rx="7" ry="3" stroke="currentColor" fill="none" strokeWidth="2" />
    <path d="M5 5v6c0 1.66 3.13 3 7 3s7-1.34 7-3V5" stroke="currentColor" fill="none" strokeWidth="2" />
    <path d="M5 11v6c0 1.66 3.13 3 7 3s7-1.34 7-3v-6" stroke="currentColor" fill="none" strokeWidth="2" />
  </svg>
);
