import { NavLink, Outlet } from 'react-router-dom'
import { Activity, Bell, LogOut, ServerCog, Settings, ShieldCheck } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { useLive } from '../context/LiveContext'

const NAV = [
  { to: '/', label: 'Supervision', icon: Activity, end: true },
  { to: '/alertes', label: 'Alertes', icon: Bell },
  { to: '/systeme', label: 'Système', icon: ServerCog },
  { to: '/admin', label: 'Administration', icon: Settings, admin: true },
]

const MainLayout = () => {
  const { user, isAdmin, logout } = useAuth()
  const { connected } = useLive()

  return (
    <div className="flex h-full flex-col bg-page text-ink md:flex-row">
      <nav className="flex shrink-0 flex-col border-b border-line bg-surface md:w-60 md:border-b-0 md:border-r">
        <div className="flex items-center justify-between gap-2.5 px-5 py-4 md:py-6">
          <div className="flex items-center gap-2.5">
            <ShieldCheck className="h-7 w-7 text-accent" aria-hidden="true" />
            <div>
              <div className="text-base font-bold leading-tight">Sentinel-X</div>
              <div className="text-[11px] uppercase tracking-wider text-muted">AetherCorp · Avant-poste</div>
            </div>
          </div>
          {/* déconnexion accessible aussi sur mobile */}
          <button
            type="button"
            onClick={logout}
            aria-label="Se déconnecter"
            className="rounded-md p-2 text-muted hover:bg-surface-2 hover:text-ink md:hidden"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>

        <ul className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:pb-0">
          {NAV.filter((n) => !n.admin || isAdmin).map(({ to, label, icon: Icon, end }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={end}
                className={({ isActive }) =>
                  `flex items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm transition-colors ${
                    isActive ? 'bg-surface-2 font-medium text-ink' : 'text-ink-2 hover:bg-surface-2 hover:text-ink'
                  }`
                }
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {label}
              </NavLink>
            </li>
          ))}
        </ul>

        <div className="mt-auto hidden space-y-3 border-t border-line p-4 md:block">
          <div className="flex items-center gap-2 text-xs text-ink-2">
            <span className={`h-2 w-2 rounded-full ${connected ? 'bg-good' : 'bg-critical'}`} aria-hidden="true" />
            {connected ? 'Flux temps réel actif' : 'Flux temps réel coupé'}
          </div>
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{user?.username}</div>
              <div className="text-xs text-muted">{isAdmin ? 'Administrateur' : 'Opérateur (lecture)'}</div>
            </div>
            <button
              type="button"
              onClick={logout}
              title="Se déconnecter"
              aria-label="Se déconnecter"
              className="rounded-md p-2 text-muted hover:bg-surface-2 hover:text-ink"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </nav>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl p-4 md:p-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

export default MainLayout
