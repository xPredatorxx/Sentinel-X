import { useState } from 'react'
import { BellOff, BellRing, Lightbulb, LightbulbOff, Volume2 } from 'lucide-react'
import { api } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import { useLive } from '../context/LiveContext'

const ACTIONS = [
  { action: 'ALARM_ON', label: 'Déclencher l\'alarme', icon: BellRing, tone: 'danger' },
  { action: 'ALARM_OFF', label: 'Arrêter l\'alarme', icon: BellOff, tone: 'default' },
  { action: 'BEEP_2', label: '2 bips', icon: Volume2, tone: 'default' },
  { action: 'LED_ON', label: 'Allumer la LED', icon: Lightbulb, tone: 'default' },
  { action: 'LED_OFF', label: 'Éteindre la LED', icon: LightbulbOff, tone: 'default' },
]

const ControlPanel = ({ nodeId }) => {
  const { isAdmin } = useAuth()
  const { nodes } = useLive()
  const [pending, setPending] = useState(null)
  const [feedback, setFeedback] = useState(null)
  const ack = nodes[nodeId]?.last_cmd

  const send = async (action) => {
    setPending(action)
    setFeedback(null)
    try {
      const r = await api('/api/v1/actuators/control', { method: 'POST', body: { node_id: nodeId, action } })
      setFeedback({ ok: true, text: `Commande #${r.command_id} transmise, en attente de confirmation du boîtier…`, id: r.command_id })
    } catch (err) {
      setFeedback({ ok: false, text: err.message })
    } finally {
      setPending(null)
    }
  }

  const confirmed = feedback?.ok && ack && ack.id === feedback.id

  if (!isAdmin) {
    return (
      <p className="text-sm text-muted">
        Lecture seule : les commandes du boîtier sont réservées au rôle administrateur.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {ACTIONS.map(({ action, label, icon: Icon, tone }) => (
          <button
            key={action}
            type="button"
            disabled={pending !== null}
            onClick={() => send(action)}
            className={`inline-flex items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium transition-colors disabled:cursor-wait disabled:opacity-60 ${
              tone === 'danger'
                ? 'border-critical/60 bg-critical/15 text-ink hover:bg-critical/25'
                : 'border-line-strong bg-surface-2 text-ink-2 hover:bg-line-strong hover:text-ink'
            }`}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
      <p className="min-h-5 text-xs" role="status" aria-live="polite">
        {confirmed ? (
          <span className="text-good">
            Commande #{ack.id} ({ack.action}) {ack.ok ? 'exécutée par le boîtier' : 'refusée par le boîtier'}.
          </span>
        ) : feedback ? (
          <span className={feedback.ok ? 'text-ink-2' : 'text-critical'}>{feedback.text}</span>
        ) : (
          <span className="text-muted">Les ordres partent en MQTTS ; le boîtier confirme via son topic de statut.</span>
        )}
      </p>
    </div>
  )
}

export default ControlPanel
