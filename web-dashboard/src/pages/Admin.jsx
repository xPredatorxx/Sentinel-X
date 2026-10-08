import { useState } from 'react'
import { BrainCircuit, KeyRound, ShieldCheck } from 'lucide-react'
import Card from '../components/Card'
import StatusBadge from '../components/StatusBadge'
import Spinner from '../components/Spinner'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'
import { fmtDateTime } from '../lib/format'

const SECURITY_POINTS = [
  'Session dans un cookie HttpOnly + Secure + SameSite=Strict (aucun jeton accessible au JavaScript)',
  'Mots de passe hachés en Argon2, verrouillage après 5 échecs, limitation de débit Nginx + API',
  'Rôles : administrateur (commandes, IA) / opérateur (lecture seule), vérifiés côté serveur',
  'Flux MQTT chiffrés (TLS 1.2+), ACL par boîtier : télémétrie, statut et commandes uniquement',
  'Modèle IA signé (HMAC-SHA256) : un fichier altéré est refusé au chargement',
]

const Admin = () => {
  const { user } = useAuth()
  const { data: ml, reload } = useApi('/api/v1/ml/status')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  const train = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const r = await api('/api/v1/ml/train', { method: 'POST' })
      setMessage({ ok: true, text: `Modèle entraîné et signé sur ${r.samples} mesures.` })
      reload()
    } catch (err) {
      setMessage({ ok: false, text: err.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Administration</h1>
        <p className="mt-1 text-sm text-muted">
          Connecté en tant que <span className="text-ink-2">{user?.username}</span> (administrateur)
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Maintenance prédictive (Isolation Forest)" icon={BrainCircuit}>
          <div className="space-y-4">
            <p className="text-sm text-ink-2">
              Le modèle apprend le fonctionnement normal du boîtier (température, humidité, gaz) à partir de
              l'historique, sans seuil fixe. Entraînez-le après au moins 10 minutes de fonctionnement normal (300 mesures), capteur de gaz préchauffé.
            </p>
            <dl className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <dt className="text-muted">État</dt>
                <dd>
                  {ml ? (
                    <StatusBadge
                      status={ml.error ? 'critical' : ml.loaded ? 'good' : 'warning'}
                      label={ml.error ? 'Modèle refusé' : ml.loaded ? 'Actif' : 'Non entraîné'}
                    />
                  ) : (
                    '—'
                  )}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">Dernier entraînement</dt>
                <dd className="text-ink-2 tabular">{ml?.trained_at ? fmtDateTime(ml.trained_at) : '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">Échantillons</dt>
                <dd className="text-ink-2 tabular">{ml?.samples ?? '—'}</dd>
              </div>
            </dl>
            <button
              type="button"
              onClick={train}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent/85 disabled:opacity-60"
            >
              {busy ? <Spinner className="h-4 w-4" /> : <BrainCircuit className="h-4 w-4" aria-hidden="true" />}
              Entraîner le modèle
            </button>
            {message && (
              <p role="status" className={`text-sm ${message.ok ? 'text-good' : 'text-critical'}`}>
                {message.text}
              </p>
            )}
          </div>
        </Card>

        <Card title="Mesures de sécurité actives" icon={ShieldCheck}>
          <ul className="space-y-2.5">
            {SECURITY_POINTS.map((p) => (
              <li key={p} className="flex gap-2.5 text-sm text-ink-2">
                <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-good" aria-hidden="true" />
                {p}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  )
}

export default Admin
