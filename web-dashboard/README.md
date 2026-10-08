# Dashboard SENTINEL-X

Interface de supervision (React 19 + Vite + Tailwind 4 + Recharts), servie en production par Nginx (HTTPS).

| Page | Rôle | Accès |
|---|---|---|
| Supervision | Mesures en direct (WebSocket), courbes 15 min → 24 h, flux caméra IA, dernières alertes, commandes du boîtier | session (commandes : admin) |
| Alertes | Historique filtrable par sévérité (horodatage serveur) | session |
| Système | Supervision technique (MCO) : API, base, broker, boîtier, IA vision, IA prédictive | session |
| Administration | Entraînement du modèle Isolation Forest, mesures de sécurité actives | admin |

Sécurité côté navigateur : aucun secret dans le code, session en cookie HttpOnly (illisible en JavaScript),
URL relatives (même origine, pas de CORS), aucun rendu HTML de données reçues, CSP stricte appliquée par Nginx.

## Développement

```bash
npm ci
npm run dev      # http://localhost:5173 — Vite relaie /api, /ws et /video_feed vers l'API locale
npm run lint
npm run build    # dist/ (copié dans l'image Nginx par le Dockerfile)
```
