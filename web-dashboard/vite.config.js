import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// En dev, Vite relaie l'API, le WebSocket et le flux vidéo : le navigateur ne voit
// qu'une seule origine (pas de CORS à ouvrir). En production, c'est Nginx qui le fait.
// changeOrigin: false conserve l'en-tête Host, utilisé par l'API pour vérifier l'origine.
const proxy = (target, extra = {}) => ({ target, changeOrigin: false, ...extra })

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': proxy('http://127.0.0.1:8000'),
      '/ws': proxy('ws://127.0.0.1:8000', { ws: true }),
      '/video_feed': proxy('http://127.0.0.1:5000'),
    },
  },
  build: {
    sourcemap: false, // pas de code source exposé en production
    chunkSizeWarningLimit: 800,
  },
})
