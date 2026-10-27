interface MetricRowProps {
  label: string;
  value: string;
  mono?: boolean;
}

export function MetricRow({ label, value, mono = false }: MetricRowProps) {
  const unavailable = value === "—" || value === "Unavailable";
  return (
    <div className="metric-row">
      <dt className="metric-row__label">{label}</dt>
      <dd
        className={[
          "metric-row__value",
          mono ? "metric-row__value--mono" : "",
          unavailable ? "metric-row__value--muted" : "",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {value}
      </dd>
    </div>
  );
}
