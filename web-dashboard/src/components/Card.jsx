const Card = ({ title, icon: Icon, action, children, className = '', bodyClassName = '' }) => (
  <section className={`flex flex-col rounded-xl border border-line bg-surface ${className}`}>
    {(title || action) && (
      <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
          {Icon && <Icon className="h-4 w-4 text-muted" aria-hidden="true" />}
          {title}
        </h2>
        {action}
      </header>
    )}
    <div className={`flex-1 p-5 ${bodyClassName}`}>{children}</div>
  </section>
)

export default Card
