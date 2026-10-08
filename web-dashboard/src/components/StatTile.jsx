const StatTile = ({ label, value, unit, sub, icon: Icon, swatch, children }) => (
  <div className="rounded-xl border border-line bg-surface px-5 py-4">
    <div className="flex items-center justify-between">
      <span className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted">
        {swatch && <span className="h-2 w-2 rounded-full" style={{ background: swatch }} aria-hidden="true" />}
        {label}
      </span>
      {Icon && <Icon className="h-4 w-4 text-muted" aria-hidden="true" />}
    </div>
    <div className="mt-2 flex items-baseline gap-1.5">
      {children ?? (
        <>
          <span className="text-3xl font-semibold text-ink">{value}</span>
          {unit && <span className="text-sm text-ink-2">{unit}</span>}
        </>
      )}
    </div>
    {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
  </div>
)

export default StatTile
