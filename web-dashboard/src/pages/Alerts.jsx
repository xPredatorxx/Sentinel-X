import { useMemo, useState } from 'react'
import Card from '../components/Card'
import StatusBadge from '../components/StatusBadge'
import Spinner from '../components/Spinner'
import { useApi } from '../hooks/useApi'
import { useLive } from '../context/LiveContext'
import { METRIC_LABELS, alertLabel, fmtDateTime, severityStatus } from '../lib/format'

const FILTERS = [
  { value: 'ALL', label: 'Toutes' },
  { value: 'CRITICAL', label: 'Critiques' },
  { value: 'WARNING', label: 'Avertissements' },
  { value: 'INFO', label: 'Informations' },
]

const Alerts = () => {
  const { liveAlerts } = useLive()
  const { data, loading, error } = useApi('/api/v1/alerts?limit=200', { interval: 30000 })
  const [filter, setFilter] = useState('ALL')

  const rows = useMemo(() => {
    const seen = new Set()
    return [...liveAlerts, ...(data || [])]
      .filter((a) => {
        if (seen.has(a.incident_id)) return false
        seen.add(a.incident_id)
        return filter === 'ALL' || a.severity === filter
      })
  }, [liveAlerts, data, filter])

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Alertes</h1>
          <p className="mt-1 text-sm text-muted">Horodatage serveur · 200 dernières alertes</p>
        </div>
        <div role="group" aria-label="Filtrer par sévérité" className="inline-flex rounded-lg border border-line bg-surface p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              aria-pressed={filter === f.value}
              onClick={() => setFilter(f.value)}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                filter === f.value ? 'bg-surface-2 text-ink' : 'text-muted hover:text-ink'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </header>

      <Card bodyClassName="p-0">
        {loading && !data ? (
          <div className="flex justify-center p-10">
            <Spinner />
          </div>
        ) : error ? (
          <p className="p-6 text-sm text-critical">{error.message}</p>
        ) : rows.length === 0 ? (
          <p className="p-10 text-center text-sm text-muted">Aucune alerte pour ce filtre.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="px-5 py-3 font-medium">Incident</th>
                  <th scope="col" className="px-5 py-3 font-medium">Date</th>
                  <th scope="col" className="px-5 py-3 font-medium">Sévérité</th>
                  <th scope="col" className="px-5 py-3 font-medium">Type</th>
                  <th scope="col" className="px-5 py-3 font-medium">Source</th>
                  <th scope="col" className="px-5 py-3 font-medium">Mesures</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((a) => (
                  <tr key={a.incident_id} className="hover:bg-surface-2/50">
                    <td className="whitespace-nowrap px-5 py-3 font-mono text-xs text-ink-2">{a.incident_id}</td>
                    <td className="whitespace-nowrap px-5 py-3 text-ink-2 tabular">{fmtDateTime(a.timestamp)}</td>
                    <td className="px-5 py-3">
                      <StatusBadge status={severityStatus(a.severity)} label={a.severity} />
                    </td>
                    <td className="whitespace-nowrap px-5 py-3 text-ink">{alertLabel(a)}</td>
                    <td className="whitespace-nowrap px-5 py-3 text-ink-2">
                      {a.source}
                      <div className="font-mono text-xs text-muted">{a.node_id}</div>
                    </td>
                    <td className="px-5 py-3 text-xs text-ink-2 tabular">
                      {Object.entries(a.metrics || {}).map(([k, v]) => (
                        <span key={k} className="mr-3 inline-block">
                          <span className="text-muted">{METRIC_LABELS[k] || k}</span>{' '}
                          {typeof v === 'number' ? Number(v.toFixed(3)) : '—'}
                        </span>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

export default Alerts
