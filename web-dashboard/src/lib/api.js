// Client HTTP du dashboard.
// - Aucune clé ni aucun secret dans le code front : l'authentification repose sur un
//   cookie de session HttpOnly posé par l'API (illisible par JavaScript).
// - Requêtes modifiantes : en-tête X-Requested-With exigé par l'API (protection CSRF).
// - URL relatives : même origine que la page, aucun CORS à ouvrir.

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

let onUnauthorized = () => {}
export const setUnauthorizedHandler = (fn) => {
  onUnauthorized = fn
}

export async function api(path, { method = 'GET', body, signal } = {}) {
  const headers = { Accept: 'application/json' }
  if (method !== 'GET') headers['X-Requested-With'] = 'sentinel-dashboard'
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let res
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
      cache: 'no-store',
      signal,
    })
  } catch (err) {
    if (err.name === 'AbortError') throw err
    throw new ApiError(0, 'Serveur injoignable')
  }

  if (res.status === 401 && path !== '/api/v1/auth/login') onUnauthorized()
  if (res.status === 204) return null

  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : `Erreur ${res.status}`
    throw new ApiError(res.status, detail)
  }
  return data
}
