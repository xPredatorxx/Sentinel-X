import StatusBadge from './StatusBadge'
import { METRIC_LABELS, alertLabel, fmtDateTime, severityStatus } from '../lib/format'

// Les champs reçus sont affichés comme du texte (React échappe tout) :
// aucune donnée issue de l'API, de l'ESP ou de l'IA n'est interprétée comme du HTML.
const metricText = (metrics) =>
  Object.entries(metrics || {})
    .slice(0, 4)
    .map(([k, v]) => `${METRIC_LABELS[k] || k} ${typeof v === 'number' ? Number(v.toFixed(3)) : ''}`)
    .join(' · ')

const AlertList = ({ alerts, compact = false }) => {
  if (!alerts.length) {
    return <p className="py-6 text-center text-sm text-muted">Aucune alerte enregistrée.</p>
  }
  return (
    <ul className="divide-y divide-line">
      {alerts.map((a) => (
        <li key={a.incident_id} className="flex items-start gap-3 py-2.5">
          <StatusBadge status={severityStatus(a.severity)} label={a.severity} className="mt-0.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="text-sm font-medium text-ink">{alertLabel(a)}</span>
              <span className="text-xs text-muted tabular">{fmtDateTime(a.timestamp)}</span>
            </div>
            <div className="truncate text-xs text-muted">
              <span className="font-mono">{a.incident_id}</span> · {a.node_id}
              {!compact && a.source ? ` · ${a.source}` : ''}
              {Object.keys(a.metrics || {}).length ? ` · ${metricText(a.metrics)}` : ''}
            </div>
          </div>
        </li>
      ))}
    </ul>
  )
}

export default AlertList
