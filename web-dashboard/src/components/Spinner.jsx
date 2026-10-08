const Spinner = ({ className = 'h-6 w-6' }) => (
  <span
    role="status"
    aria-label="Chargement"
    className={`inline-block animate-spin rounded-full border-2 border-line-strong border-t-accent ${className}`}
  />
)

export default Spinner
