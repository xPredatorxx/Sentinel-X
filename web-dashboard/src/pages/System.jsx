import { Activity, BrainCircuit, Camera, Cpu, Database, RadioTower, Router, Server } from 'lucide-react'
import Card from '../components/Card'
import StatusBadge from '../components/StatusBadge'
import Spinner from '../components/Spinner'
import { useApi } from '../hooks/useApi'
import { fmtDateTime, fmtDuration, fmtNumber } from '../lib/format'

// Supervision technique (MCO) : état de chaque brique, rafraîchi toutes les 5 s.
const ServiceCard = ({ icon: Icon, name, status, label, children }) => (
  <div className="rounded-xl border border-line bg-surface p-5">
    <div className="flex items-start justify-between gap-3">
      <div className="flex min-w-0 items-center gap-2.5">
        <Icon className="h-5 w-5 text-muted" aria-hidden="true" />
        <h2 className="truncate text-sm font-semibold" title={name}>{name}</h2>
      </div>
      <StatusBadge status={status} label={label} />
    </div>
    <dl className="mt-4 space-y-1.5 text-xs">{children}</dl>
  </div>
)

const Row = ({ label, value }) => (
  <div className="flex justify-between gap-3">
    <dt className="text-muted">{label}</dt>
    <dd className="text-right text-ink-2 tabular">{value ?? '—'}</dd>
  </div>
)

const System = () => {
  const { data: s, error, loading } = useApi('/api/v1/system/status', { interval: 5000 })

  if (loading && !s) {
    return (
      <div className="flex justify-center p-16">
        <Spinner />
      </div>
    )
  }
  if (!s) return <p className="text-sm text-critical">{error?.message || 'État indisponible'}</p>

  const node = s.nodes?.[0]
  const c = s.counters || {}
  const allUp = s.database.up && s.mqtt.up && s.ai_vision.up && node?.online

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Système</h1>
          <p className="mt-1 text-sm text-muted">
            Maintien en condition opérationnelle · heure serveur {fmtDateTime(s.server_time)}
          </p>
        </div>
        <StatusBadge
          status={allUp ? 'good' : 'warning'}
          label={allUp ? 'Tous les services sont opérationnels' : 'Service dégradé'}
          className="text-sm"
        />
      </header>

      {error && (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
          Dernier rafraîchissement en échec : {error.message}
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <ServiceCard icon={Server} name="API FastAPI" status="good" label="Opérationnelle">
          <Row label="Version" value={s.api.version} />
          <Row label="Disponibilité" value={fmtDuration(s.api.uptime_s)} />
          <Row label="Clients temps réel" value={s.websocket_clients} />
        </ServiceCard>

        <ServiceCard
          icon={Database}
          name="PostgreSQL"
          status={s.database.up ? 'good' : 'critical'}
          label={s.database.up ? 'Opérationnelle' : 'Injoignable'}
        >
          <Row label="Mesures stockées" value={s.database.telemetry?.toLocaleString('fr-FR')} />
          <Row label="Alertes stockées" value={s.database.alerts?.toLocaleString('fr-FR')} />
          <Row label="Exposition" value="réseau interne uniquement" />
        </ServiceCard>

        <ServiceCard
          icon={RadioTower}
          name="Broker MQTT"
          status={s.mqtt.up ? 'good' : 'critical'}
          label={s.mqtt.up ? 'Connecté (TLS)' : 'Déconnecté'}
        >
          <Row label="Messages reçus" value={c.mqtt_received} />
          <Row label="Messages rejetés" value={c.mqtt_rejected} />
          <Row label="Commandes envoyées" value={c.commands_sent} />
          {s.mqtt.error && <Row label="Dernière erreur" value={s.mqtt.error} />}
        </ServiceCard>

        <ServiceCard
          icon={Router}
          name={`Boîtier ${node?.node_id || ''}`}
          status={node?.online ? 'good' : 'critical'}
          label={node?.online ? 'En ligne' : 'Hors ligne'}
        >
          <Row label="Dernier contact" value={node?.last_seen ? fmtDateTime(node.last_seen) : '—'} />
          <Row label="Il y a" value={node?.age_s != null ? fmtDuration(node.age_s) : '—'} />
          <Row label="Alarme" value={node?.alarm ? 'active' : 'inactive'} />
          <Row
            label="Dernière commande"
            value={node?.last_cmd ? `#${node.last_cmd.id} ${node.last_cmd.action || ''} ${node.last_cmd.ok ? '✓' : '✗'}` : '—'}
          />
        </ServiceCard>

        <ServiceCard
          icon={Camera}
          name="IA vision"
          status={!s.ai_vision.up ? 'critical' : s.ai_vision.camera ? 'good' : 'warning'}
          label={!s.ai_vision.up ? 'Injoignable' : s.ai_vision.camera ? 'Caméra active' : 'Sans caméra'}
        >
          <Row label="Modèles chargés" value={s.ai_vision.model ? 'oui (empreintes vérifiées)' : 'non'} />
          <Row label="Images / s" value={fmtNumber(s.ai_vision.fps)} />
          <Row label="Inférence" value={s.ai_vision.inference_ms != null ? `${fmtNumber(s.ai_vision.inference_ms, 0)} ms` : '—'} />
        </ServiceCard>

        <ServiceCard
          icon={BrainCircuit}
          name="IA prédictive"
          status={s.ml.error ? 'critical' : s.ml.loaded ? 'good' : 'warning'}
          label={s.ml.error ? 'Modèle refusé' : s.ml.loaded ? 'Modèle actif' : 'Non entraîné'}
        >
          <Row label="Algorithme" value="Isolation Forest" />
          <Row label="Entraîné le" value={s.ml.trained_at ? fmtDateTime(s.ml.trained_at) : '—'} />
          <Row label="Échantillons" value={s.ml.samples} />
          {s.ml.error && <Row label="Erreur" value={s.ml.error} />}
        </ServiceCard>
      </div>

      <Card title="Compteurs depuis le démarrage" icon={Activity}>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          {[
            ['Mesures enregistrées', c.telemetry_saved],
            ['Alertes créées', c.alerts_created],
            ['Messages MQTT', c.mqtt_received],
            ['Messages rejetés', c.mqtt_rejected],
            ['Commandes', c.commands_sent],
          ].map(([label, value]) => (
            <div key={label}>
              <div className="text-2xl font-semibold tabular">{value ?? 0}</div>
              <div className="text-xs text-muted">{label}</div>
            </div>
          ))}
        </div>
        {c.last_rejection && (
          <p className="mt-4 flex items-start gap-2 text-xs text-muted">
            <Cpu className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>
              Dernier rejet : <span className="font-mono text-ink-2">{c.last_rejection}</span>
            </span>
          </p>
        )}
      </Card>
    </div>
  )
}

export default System
