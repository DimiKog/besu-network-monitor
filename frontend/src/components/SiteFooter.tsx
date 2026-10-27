import { getChainId, getNetworkName } from "@/lib/config";

export function SiteFooter() {
  const networkName = getNetworkName();
  const chainId = getChainId();

  return (
    <footer className="site-footer">
      <div className="site-footer__brand">
        <p className="site-footer__title">Besu Network Monitor</p>
        <p className="site-footer__meta">
          {networkName} · Chain ID <span className="mono">{chainId}</span>
        </p>
      </div>
      <p className="site-footer__note">
        Hyperledger Besu / QBFT · Data from live network and monitoring
        infrastructure
      </p>
    </footer>
  );
}
