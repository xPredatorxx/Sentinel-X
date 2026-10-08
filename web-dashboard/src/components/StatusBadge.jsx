import { AlertOctagon, AlertTriangle, CheckCircle2, CircleDashed, Info } from 'lucide-react'

// Statut = couleur + icône + libellé : jamais la couleur seule.
const STYLES = {
  good: { icon: CheckCircle2, color: 'text-good', ring: 'border-good/40 bg-good/10' },
  warning: { icon: AlertTriangle, color: 'text-warning', ring: 'border-warning/40 bg-warning/10' },
  critical: { icon: AlertOctagon, color: 'text-critical', ring: 'border-critical/50 bg-critical/10' },
  info: { icon: Info, color: 'text-accent', ring: 'border-accent/40 bg-accent/10' },
  unknown: { icon: CircleDashed, color: 'text-muted', ring: 'border-line-strong bg-surface-2' },
}

const StatusBadge = ({ status = 'unknown', label, className = '' }) => {
  const s = STYLES[status] || STYLES.unknown
  const Icon = s.icon
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium text-ink-2 ${s.ring} ${className}`}
    >
      <Icon className={`h-3.5 w-3.5 ${s.color}`} aria-hidden="true" />
      {label}
    </span>
  )
}

export default StatusBadge
