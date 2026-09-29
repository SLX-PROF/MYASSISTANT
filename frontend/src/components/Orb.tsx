export type OrbState = "idle" | "thinking" | "speaking";

/** The assistant's visual identity: a softly pulsing gradient sphere. */
export function Orb({ state = "idle", size, small = false, className = "" }: { state?: OrbState; size?: number; small?: boolean; className?: string }) {
  const style = size ? ({ "--size": `${size}px` } as React.CSSProperties) : undefined;
  return (
    <div className={`orb ${small ? "small" : ""} ${className}`} data-state={state} style={style} aria-hidden="true">
      {!small && <div className="orb-halo" />}
      {!small && <div className="orb-ring" />}
      <div className="orb-core" />
    </div>
  );
}

/** Decorative circuit-board lines in the background. */
export function Circuit() {
  return (
    <svg className="circuit" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="1">
        <path d="M0 120 H180 L220 160 H520" opacity="0.22" />
        <path d="M1440 80 H1240 L1200 120 H860" opacity="0.18" />
        <path d="M0 780 H140 L180 740 H460" opacity="0.16" />
        <path d="M1440 830 H1120 L1080 790 H760" opacity="0.16" />
        <circle cx="520" cy="160" r="3" fill="currentColor" opacity="0.35" />
        <circle cx="860" cy="120" r="3" fill="currentColor" opacity="0.3" />
        <circle cx="460" cy="740" r="3" fill="currentColor" opacity="0.28" />
        <circle cx="760" cy="790" r="3" fill="currentColor" opacity="0.28" />
      </g>
    </svg>
  );
}
