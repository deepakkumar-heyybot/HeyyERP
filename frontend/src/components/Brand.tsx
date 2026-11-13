export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "brand-lockup brand-lockup--compact" : "brand-lockup"}>
      <svg className="brand-logo" viewBox="0 0 48 48" role="img" aria-label="heyyERP">
        <path d="M24 4 41 14v20L24 44 7 34V14L24 4Z" fill="none" stroke="currentColor" strokeWidth="4" strokeLinejoin="round" />
        <path d="m14 18 10 6 10-6M24 24v12M14 18l10-6 10 6v12l-10 6-10-6V18Z" fill="none" stroke="currentColor" strokeWidth="3" strokeLinejoin="round" />
      </svg>
      {!compact && (
        <span className="brand-copy">
          <strong>heyyERP</strong>
          <small>V-Rub Suspensions</small>
        </span>
      )}
    </div>
  );
}
