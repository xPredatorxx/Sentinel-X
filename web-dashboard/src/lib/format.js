export const fmtTime = (t) =>
  new Date(t).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

export const fmtShortTime = (t) =>
  new Date(t).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })

export const fmtDateTime = (t) =>
  new Date(t).toLocaleString('fr-FR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

export const fmtNumber = (v, digits = 1) =>
  v === null || v === undefined ? '—' : v.toLocaleString('fr-FR', { maximumFractionDigits: digits, minimumFractionDigits: digits })

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.floor(seconds)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d) return `${d} j ${h} h`
  if (h) return `${h} h ${m} min`
  if (m) return `${m} min`
  return `${s} s`
}

export const ALERT_LABELS = {
  INTRUDER: 'Intrus détecté',
  MOTION: 'Mouvement (PIR)',
  ANOMALY: 'Anomalie (IA prédictive)',
  GAS_LEAK: 'Fuite de gaz',
  OVERHEAT: 'Surchauffe',
}

export const METRIC_LABELS = {
  confidence: 'confiance',
  persons: 'personnes',
  unknown_faces: 'visages inconnus',
  temp: 'temp.',
  hum: 'hum.',
  gas_raw: 'gaz',
  score: 'score',
  motion: 'mouvement',
  authorized: 'autorisé',
}

export const severityStatus = (severity) =>
  ({ CRITICAL: 'critical', WARNING: 'warning', INFO: 'info' })[severity] || 'unknown'

// Libellé d'une alerte : une présence reconnue par l'IA vision est distinguée d'un mouvement PIR
export const alertLabel = (a) => {
  if (a.alert_type === 'MOTION' && a.metrics?.authorized) return 'Présence autorisée (visage reconnu)'
  if (a.alert_type === 'INTRUDER') return 'Intrus détecté (caméra)'
  return ALERT_LABELS[a.alert_type] || a.alert_type
}
