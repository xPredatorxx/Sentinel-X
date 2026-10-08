import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'

// Charge une ressource de l'API ; rechargement optionnel à intervalle régulier.
export function useApi(path, { interval } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)

  const reload = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    if (!path) return undefined
    const ctrl = new AbortController()
    let retry
    api(path, { signal: ctrl.signal })
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return
        setError(err)
        // échec réseau ou serveur occupé : nouvel essai rapide (pas pour 401/403/404)
        if (!err.status || err.status === 429 || err.status >= 500) retry = setTimeout(reload, 3000)
      })
      .finally(() => {
        // une requête annulée (démontage, changement d'URL) ne termine pas le chargement
        if (!ctrl.signal.aborted) setLoading(false)
      })
    return () => {
      clearTimeout(retry)
      ctrl.abort()
    }
  }, [path, tick, reload])

  useEffect(() => {
    if (!interval) return undefined
    const id = setInterval(reload, interval)
    return () => clearInterval(id)
  }, [interval, reload])

  return { data, error, loading, reload }
}
