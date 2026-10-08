import { createContext, useContext, useEffect, useRef, useState } from 'react'

const LiveContext = createContext(null)
const MAX_POINTS = 1800 // ~1 h à une mesure toutes les 2 s

// Valeur numérique finie ou null : les données reçues ne sont jamais crues sur parole.
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

export function LiveProvider({ children }) {
  const [connected, setConnected] = useState(false)
  const [nodes, setNodes] = useState({})
  const [latest, setLatest] = useState(null)
  const [points, setPoints] = useState([])
  const [liveAlerts, setLiveAlerts] = useState([])
  const retry = useRef(0)

  useEffect(() => {
    let ws
    let timer
    let closed = false

    const connect = () => {
      // même hôte que la page ; wss:// derrière Nginx (le cookie de session suit)
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${window.location.host}/ws/live`)

      ws.onopen = () => {
        retry.current = 0
        setConnected(true)
      }
      ws.onmessage = (event) => {
        let msg
        try {
          msg = JSON.parse(event.data)
        } catch {
          return
        }
        if (!msg || typeof msg !== 'object') return
        if (msg.type === 'hello' && Array.isArray(msg.nodes)) {
          setNodes(Object.fromEntries(msg.nodes.map((n) => [n.node_id, n])))
        } else if (msg.type === 'status' && msg.data?.node_id) {
          setNodes((prev) => ({ ...prev, [msg.data.node_id]: msg.data }))
        } else if (msg.type === 'telemetry' && msg.data) {
          const t = Date.parse(msg.received_at)
          if (Number.isNaN(t)) return
          const d = msg.data
          const point = {
            t,
            temp: num(d.temp),
            hum: num(d.hum),
            gas_raw: num(d.gas_raw),
            gas_v: num(d.gas_v),
            motion: d.motion === true,
            rssi: num(d.rssi),
          }
          setLatest({ ...point, node_id: msg.node_id })
          setPoints((prev) => [...prev, point].slice(-MAX_POINTS))
          setNodes((prev) => ({
            ...prev,
            [msg.node_id]: { ...(prev[msg.node_id] || {}), node_id: msg.node_id, online: true, last_seen: msg.received_at },
          }))
        } else if (msg.type === 'alert' && msg.data?.incident_id) {
          setLiveAlerts((prev) => [msg.data, ...prev].slice(0, 100))
        }
      }
      ws.onclose = (event) => {
        setConnected(false)
        if (closed) return
        // 1008 = session refusée : inutile d'insister, la page de connexion prendra le relais
        if (event.code === 1008) return
        const delay = Math.min(30000, 1000 * 2 ** retry.current++)
        timer = setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      closed = true
      clearTimeout(timer)
      ws?.close()
    }
  }, [])

  return (
    <LiveContext.Provider value={{ connected, nodes, latest, points, liveAlerts }}>
      {children}
    </LiveContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export const useLive = () => useContext(LiveContext)
