import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Lock, ShieldCheck } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import Spinner from '../components/Spinner'

const inputClass =
  'w-full rounded-lg border border-line-strong bg-page px-3 py-2.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none'

const Login = () => {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/" replace />

  const handleSubmit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      // le mot de passe est vérifié par l'API (hachage Argon2) ; rien n'est stocké côté navigateur
      await login(username.trim(), password)
      const from = location.state?.from
      // redirection interne uniquement (pas de redirection ouverte vers un autre site)
      const safe = typeof from === 'string' && from.startsWith('/') && !from.startsWith('//')
      navigate(safe ? from : '/', { replace: true })
    } catch (err) {
      setError(err.status === 429 ? 'Trop de tentatives. Réessayez dans quelques minutes.' : err.message)
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-page p-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <ShieldCheck className="h-10 w-10 text-accent" aria-hidden="true" />
          <h1 className="mt-3 text-xl font-bold">Sentinel-X</h1>
          <p className="mt-1 text-sm text-muted">Console de supervision de l'avant-poste</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 rounded-xl border border-line bg-surface p-6">
          <div>
            <label htmlFor="username" className="mb-1.5 block text-xs font-medium text-ink-2">
              Identifiant
            </label>
            <input
              id="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              maxLength={64}
              required
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor="password" className="mb-1.5 block text-xs font-medium text-ink-2">
              Mot de passe
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value)
                setError('')
              }}
              maxLength={256}
              required
              className={inputClass}
            />
          </div>

          {error && (
            <p role="alert" className="rounded-md border border-critical/40 bg-critical/10 px-3 py-2 text-sm text-ink">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-accent/85 disabled:opacity-60"
          >
            {busy ? <Spinner className="h-4 w-4" /> : <Lock className="h-4 w-4" aria-hidden="true" />}
            Se connecter
          </button>
        </form>
        <p className="mt-4 text-center text-xs text-muted">Connexion chiffrée (TLS) · accès journalisé</p>
      </div>
    </div>
  )
}

export default Login
