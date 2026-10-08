import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { fmtNumber, fmtShortTime, fmtTime } from '../lib/format'

// Une série par graphique (pas de double axe) : ligne 2 px, aplat léger, info-bulle au survol.
const ChartTooltip = ({ active, payload, unit, digits }) => {
  if (!active || !payload?.length) return null
  const p = payload[0]
  return (
    <div className="rounded-lg border border-line-strong bg-surface-2 px-3 py-2 text-xs shadow-lg">
      <div className="text-muted tabular">{fmtTime(p.payload.t)}</div>
      <div className="mt-0.5 flex items-center gap-2 text-ink">
        <span className="h-2 w-2 rounded-full" style={{ background: p.color }} aria-hidden="true" />
        <span className="font-semibold tabular">
          {fmtNumber(p.value, digits)} {unit}
        </span>
      </div>
    </div>
  )
}

// Axe Y avec une amplitude minimale : un capteur au pas de 0,1 °C ne doit pas
// produire un « peigne » plein écran pour une variation insignifiante.
const paddedDomain = (minSpan, floor) => ([min, max]) => {
  const mid = (min + max) / 2
  const half = Math.max((max - min) / 2, minSpan / 2) * 1.15
  const lo = Math.floor(mid - half)
  return [floor !== undefined ? Math.max(floor, lo) : lo, Math.ceil(mid + half)]
}

const TimeSeriesChart = ({ data, dataKey, color, unit, digits = 1, minSpan = 4, floor, height = 180 }) => {
  const domain = paddedDomain(minSpan, floor)
  const hasData = data.some((d) => d[dataKey] !== null && d[dataKey] !== undefined)
  if (!hasData) {
    return (
      <div className="flex items-center justify-center text-sm text-muted" style={{ height }}>
        En attente de mesures…
      </div>
    )
  }
  const gradientId = `grad-${dataKey}`
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.25} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#2c2c2a" vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          scale="time"
          domain={['dataMin', 'dataMax']}
          tickFormatter={fmtShortTime}
          stroke="#383835"
          tick={{ fill: '#898781', fontSize: 11 }}
          tickLine={false}
          minTickGap={48}
        />
        <YAxis
          domain={domain}
          stroke="#383835"
          tick={{ fill: '#898781', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={48}
          tickFormatter={(v) => fmtNumber(v, 0)}
          allowDecimals={false}
        />
        <Tooltip
          content={<ChartTooltip unit={unit} digits={digits} />}
          cursor={{ stroke: '#898781', strokeDasharray: '3 3' }}
        />
        <Area
          type="monotone"
          dataKey={dataKey}
          stroke={color}
          strokeWidth={2}
          fill={`url(#${gradientId})`}
          dot={false}
          activeDot={{ r: 4, stroke: '#1a1a19', strokeWidth: 2 }}
          connectNulls
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

export default TimeSeriesChart
