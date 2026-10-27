/** Display helpers — never invent values when the API returns null. */

export function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatSeconds(
  value: number | null | undefined,
  {
    digits = 1,
    unavailable = "—",
  }: { digits?: number; unavailable?: string } = {},
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return unavailable;
  }
  const rounded = Number(value.toFixed(digits));
  return `${rounded}s`;
}

export function formatPercent(
  value: number | null | undefined,
  { digits = 1, unavailable = "—" }: { digits?: number; unavailable?: string } = {},
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return unavailable;
  }
  return `${Number(value.toFixed(digits))}%`;
}

export function formatUpdatedAt(iso: string | null | undefined): string {
  if (!iso) {
    return "Unavailable";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "Unavailable";
  }
  return (
    new Intl.DateTimeFormat("en-GB", {
      dateStyle: "medium",
      timeStyle: "medium",
      timeZone: "UTC",
    }).format(date) + " UTC"
  );
}

export function formatRelativeAge(
  iso: string | null | undefined,
  nowMs: number = Date.now(),
): string {
  if (!iso) {
    return "Unavailable";
  }
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) {
    return "Unavailable";
  }
  const deltaSec = Math.max(0, Math.round((nowMs - then) / 1000));
  if (deltaSec < 5) {
    return "just now";
  }
  if (deltaSec < 60) {
    return `${deltaSec}s ago`;
  }
  const minutes = Math.floor(deltaSec / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

/** Human-readable age from API age_seconds (not wall-clock). */
export function formatAgeSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  const sec = Math.max(0, Math.round(value));
  if (sec < 60) {
    return `${sec}s`;
  }
  const minutes = Math.floor(sec / 60);
  const rem = sec % 60;
  if (minutes < 60) {
    return rem === 0 ? `${minutes}m` : `${minutes}m ${rem}s`;
  }
  const hours = Math.floor(minutes / 60);
  const remMin = minutes % 60;
  return remMin === 0 ? `${hours}h` : `${hours}h ${remMin}m`;
}

export function formatQuorumState(hasQuorum: boolean | null | undefined): string {
  if (hasQuorum === null || hasQuorum === undefined) {
    return "Unavailable";
  }
  return hasQuorum ? "Quorum met" : "Quorum not met";
}

export function formatFreshness(state: string | null | undefined): string {
  if (!state || state === "unknown") {
    return "Unavailable";
  }
  if (state === "fresh") {
    return "Fresh";
  }
  if (state === "stale") {
    return "Stale";
  }
  return state;
}

export function formatSystemsOperational(
  up: number | null | undefined,
  monitored: number | null | undefined,
): string {
  if (
    up === null ||
    up === undefined ||
    monitored === null ||
    monitored === undefined
  ) {
    return "Unavailable";
  }
  return `${formatInteger(up)} / ${formatInteger(monitored)}`;
}

export function formatOnlineStatus(value: boolean | null | undefined): string {
  if (value === null || value === undefined) {
    return "Unavailable";
  }
  return value ? "Online" : "Unavailable";
}

export function shortenAddress(address: string | null | undefined): string {
  if (!address) {
    return "—";
  }
  const normalized = address.trim();
  if (normalized.length <= 12) {
    return normalized;
  }
  return `${normalized.slice(0, 6)}…${normalized.slice(-4)}`;
}
