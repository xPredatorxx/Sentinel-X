import { useState } from 'react'
import { CameraOff, RefreshCw } from 'lucide-react'

// Flux MJPEG servi par Nginx sur la même origine (/video_feed), réservé aux sessions valides.
const VisionFeed = () => {
  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(false)

  return (
    <div className="relative aspect-[4/3] w-full overflow-hidden rounded-lg border border-line bg-black">
      {!failed ? (
        <>
          <img
            key={attempt}
            src={`/video_feed?r=${attempt}`}
            alt="Flux caméra analysé par l'IA (YOLOv8 + reconnaissance faciale)"
            className="h-full w-full object-contain"
            onError={() => setFailed(true)}
          />
          <span className="absolute left-2 top-2 inline-flex items-center gap-1.5 rounded bg-black/70 px-2 py-0.5 text-[11px] font-semibold tracking-wider text-ink">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-critical" aria-hidden="true" />
            EN DIRECT
          </span>
        </>
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
          <CameraOff className="h-8 w-8 text-muted" aria-hidden="true" />
          <p className="text-sm text-ink-2">Flux vidéo indisponible</p>
          <p className="text-xs text-muted">Le service d'IA vision ne répond pas.</p>
          <button
            type="button"
            onClick={() => {
              setFailed(false)
              setAttempt((a) => a + 1)
            }}
            className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-line-strong px-3 py-1.5 text-xs text-ink-2 hover:bg-surface-2"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            Réessayer
          </button>
        </div>
      )}
    </div>
  )
}

export default VisionFeed
