import { getChainId, getNetworkName } from "@/lib/config";
import { formatRelativeAge } from "@/lib/format";

interface HeaderProps {
  updatedAt: string | null;
  live: boolean;
  nowMs?: number;
}

export function Header({ updatedAt, live, nowMs }: HeaderProps) {
  const networkName = getNetworkName();
  const chainId = getChainId();
  const relative = formatRelativeAge(updatedAt, nowMs);

  return (
    <header className="site-header">
      <div className="site-header__brand">
        <h1 className="site-header__title">Besu Network Monitor</h1>
        <p className="site-header__subtitle">
          Real-time operational visibility for Hyperledger Besu / QBFT networks
        </p>
      </div>

      <dl className="site-header__meta">
        <div className="site-header__meta-item">
          <dt>Network</dt>
          <dd>{networkName}</dd>
        </div>
        <div className="site-header__meta-item">
          <dt>Chain ID</dt>
          <dd className="mono">{chainId}</dd>
        </div>
        <div className="site-header__meta-item">
          <dt>Status</dt>
          <dd className="site-header__live">
            <span
              className={`live-dot ${live ? "live-dot--on" : "live-dot--off"}`}
              aria-hidden="true"
            />
            <span>{live ? "Live" : "Offline"}</span>
            <span className="site-header__updated" aria-live="polite">
              {updatedAt ? `Updated ${relative}` : "Awaiting data"}
            </span>
          </dd>
        </div>
      </dl>
    </header>
  );
}
