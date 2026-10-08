import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import Spinner from './Spinner'

// Garde de navigation (confort d'interface uniquement) : la vraie protection
// est côté API, qui vérifie la session et le rôle à chaque requête.
const ProtectedRoute = ({ children, role }) => {
  const { user } = useAuth()
  const location = useLocation()

  if (user === undefined) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  if (role && user.role !== role) return <Navigate to="/" replace />
  return children
}

export default ProtectedRoute
