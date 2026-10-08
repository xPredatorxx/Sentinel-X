import { useMemo, useState } from 'react'
import { Bell, Camera, Droplets, Flame, Footprints, Gamepad2, Radio, Thermometer } from 'lucide-react'
import { Link } from 'react-router-dom'
import Card from '../components/Card'
import StatTile from '../components/StatTile'
import StatusBadge from '../components/StatusBadge'
import TimeSeriesChart from '../components/TimeSeriesChart'
import VisionFeed from '../components/VisionFeed'
import AlertList from '../components/AlertList'
import ControlPanel from '../components/ControlPanel'
import { useApi } from '../hooks/useApi'
import { useLive } from '../context/LiveContext'
import { fmtNumber, fmtTime } from '../lib/format'

const RANGES = [
  { value: '15m', label: '15 min', ms: 15 * 60e3 },
  { value: '1h', label: '1 h', ms: 60 * 60e3 },
  { value: '6h', label: '6 h', ms: 6 * 3600e3 },
  { value: '24h', label: '24 h', ms: 24 * 3600e3 },
]

const COLORS = { temp: '#3987e5', hum: '#199e70', gas: '#d95926' }

const Dashboard = () => {
  const { connected, nodes, latest, points, liveAlerts } = useLive()
  const [range, setRange] = useState('15m')
  const { data: nodeList } = useApi('/api/v1/nodes')
  const nodeId = nodeList?.[0]?.node_id
  const { data: history } = useApi(nodeId ? `/api/v1/telemetry/history?range=${range}&node_id=${nodeId}` : null, {
    interval: 60000,
  })
  const { data: lastStored } = useApi(nodeId ? `/api/v1/telemetry/latest?node_id=${nodeId}` : null)
  const { data: storedAlerts } = useApi('/api/v1/alerts?limit=8', { interval: 60000 })

  // historique (REST) + mesures temps réel (WebSocket) postérieures, sur la fenêtre choisie
  const series = useMemo(() => {
    const windowMs = RANGES.find((r) => r.value === range).ms
    const hist = (history?.points || []).map((p) => ({ ...p, t: Date.parse(p.t) }))
    const lastT = hist.length ? hist[hist.length - 1].t : 0
    const merged = [...hist, ...points.filter((p) => p.t > lastT)]
    // fenêtre ancrée sur la mesure la plus récente (calcul pur, sans horloge locale)
    const newest = merged.length ? merged[merged.length - 1].t : 0
    return merged.filter((p) => p.t >= newest - windowMs)
  }, [history, points, range])

  const alerts = useMemo(() => {
    const seen = new Set()
    return [...liveAlerts, ...(storedAlerts || [])]
      .filter((a) => !seen.has(a.incident_id) && seen.add(a.incident_id))
      .slice(0, 8)
  }, [liveAlerts, storedAlerts])

  const node = nodeId ? nodes[nodeId] || nodeList?.[0] : null
  const cur = latest || (lastStored && { ...lastStored, t: Date.parse(lastStored.received_at) })
  const online = node?.online === true

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Supervision</h1>
          <p className="mt-1 text-sm text-muted">
            Boîtier <span className="font-mono text-ink-2">{nodeId || '—'}</span>
            {cur?.t ? <> · dernière mesure à <span className="tabular">{fmtTime(cur.t)}</span></> : null}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={online ? 'good' : 'critical'} label={online ? 'Boîtier en ligne' : 'Boîtier hors ligne'} />
          <StatusBadge status={connected ? 'good' : 'warning'} label={connected ? 'Temps réel' : 'Reconnexion…'} />
          {node?.alarm && <StatusBadge status="critical" label="Alarme active" />}
        </div>
      </header>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatTile label="Température" icon={Thermometer} swatch={COLORS.temp} value={fmtNumber(cur?.temp)} unit="°C" sub="DHT22" />
        <StatTile label="Humidité" icon={Droplets} swatch={COLORS.hum} value={fmtNumber(cur?.hum, 0)} unit="%" sub="DHT22" />
        <StatTile
          label="Gaz"
          icon={Flame}
          swatch={COLORS.gas}
          value={fmtNumber(cur?.gas_raw, 0)}
          unit="/ 4095"
          sub={cur?.gas_v != null ? `MQ-2 · ${fmtNumber(cur.gas_v, 2)} V` : 'MQ-2 · valeur brute'}
        />
        <StatTile label="Présence" icon={Footprints} sub="Capteur PIR">
          {cur ? (
            <StatusBadge status={cur.motion ? 'warning' : 'good'} label={cur.motion ? 'Mouvement détecté' : 'Aucun mouvement'} className="text-sm" />
          ) : (
            <span className="text-3xl font-semibold text-ink">—</span>
          )}
        </StatTile>
        <StatTile
          label="Signal Wi-Fi"
          icon={Radio}
          value={fmtNumber(cur?.rssi, 0)}
          unit="dBm"
          sub={cur?.rssi == null ? 'MQTTS · TLS 1.2' : cur.rssi > -67 ? 'Bon · MQTTS' : cur.rssi > -80 ? 'Moyen · MQTTS' : 'Faible · MQTTS'}
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink-2">Historique des capteurs</h2>
        <div role="group" aria-label="Période" className="inline-flex rounded-lg border border-line bg-surface p-0.5">
          {RANGES.map((r) => (
            <button
              key={r.value}
              type="button"
              aria-pressed={range === r.value}
              onClick={() => setRange(r.value)}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                range === r.value ? 'bg-surface-2 text-ink' : 'text-muted hover:text-ink'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="Température (°C)" icon={Thermometer}>
          <TimeSeriesChart data={series} dataKey="temp" color={COLORS.temp} unit="°C" minSpan={4} />
        </Card>
        <Card title="Humidité (%)" icon={Droplets}>
          <TimeSeriesChart data={series} dataKey="hum" color={COLORS.hum} unit="%" digits={0} minSpan={10} floor={0} />
        </Card>
        <Card title="Gaz (valeur brute ADC)" icon={Flame}>
          <TimeSeriesChart data={series} dataKey="gas_raw" color={COLORS.gas} unit="" digits={0} minSpan={200} floor={0} />
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <Card title="Vision IA (YOLOv8 + reconnaissance faciale)" icon={Camera} className="lg:col-span-3">
          <VisionFeed />
        </Card>
        <div className="flex flex-col gap-4 lg:col-span-2">
          <Card
            title="Dernières alertes"
            icon={Bell}
            action={
              <Link to="/alertes" className="text-xs text-accent hover:underline">
                Tout voir
              </Link>
            }
            bodyClassName="py-1"
          >
            <AlertList alerts={alerts} compact />
          </Card>
          <Card title="Contrôle du boîtier" icon={Gamepad2}>
            {nodeId ? <ControlPanel nodeId={nodeId} /> : <p className="text-sm text-muted">Aucun boîtier configuré.</p>}
          </Card>
        </div>
      </div>
    </div>
  )
}

export default Dashboard
