import { presentDomainHealth } from "@/lib/health";
import type { PublicHealth } from "@/lib/types";

interface StatusBadgeProps {
  health: PublicHealth;
  size?: "sm" | "md";
}

export function StatusBadge({ health, size = "sm" }: StatusBadgeProps) {
  const label = presentDomainHealth(health);
  return (
    <span
      className={`status-badge status-badge--${health} status-badge--${size}`}
      role="status"
      aria-label={`Health: ${label}`}
    >
      <span className="status-badge__dot" aria-hidden="true" />
      <span className="status-badge__label">{label}</span>
    </span>
  );
}
