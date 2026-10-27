import type { ReactNode } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import type { PublicHealth } from "@/lib/types";

interface SectionShellProps {
  id: string;
  title: string;
  description?: string;
  health?: PublicHealth;
  meta?: ReactNode;
  error?: string | null;
  children: ReactNode;
}

export function SectionShell({
  id,
  title,
  description,
  health,
  meta,
  error,
  children,
}: SectionShellProps) {
  return (
    <section className="detail-section" aria-labelledby={id}>
      <header className="detail-section__header">
        <div className="detail-section__heading">
          <div className="detail-section__title-row">
            <h2 id={id} className="detail-section__title">
              {title}
            </h2>
            {health ? <StatusBadge health={health} /> : null}
          </div>
          {description ? (
            <p className="detail-section__description">{description}</p>
          ) : null}
        </div>
        {meta ? <div className="detail-section__meta">{meta}</div> : null}
      </header>

      {error ? (
        <p className="section-refresh-warning" role="status">
          Latest refresh failed: {error}. Showing last successful data.
        </p>
      ) : null}

      <div className="detail-section__body">{children}</div>
    </section>
  );
}
