import type { ReactNode } from "react";

/** Shimmering rectangle for loading placeholders. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden="true" className={`skeleton ${className}`} />;
}

/** Skeleton block for the project detail page while data loads. */
export function ProjectDetailSkeleton() {
  return (
    <div className="space-y-8" role="status" aria-label="Loading project">
      <div className="space-y-3">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-9 w-80 max-w-full" />
        <Skeleton className="h-4 w-64 max-w-full" />
      </div>
      <div className="card p-6">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="mt-4 h-11 w-full" />
        <Skeleton className="mt-3 h-4 w-72 max-w-full" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
      </div>
    </div>
  );
}

/** Full-page skeleton for list pages. */
export function ListSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-20 w-full" />
      ))}
    </div>
  );
}

/** Full-viewport loading state used by auth-gated pages. */
export function FullPageLoader() {
  return (
    <div className="flex flex-1 items-center justify-center" role="status" aria-label="Loading">
      <div className="flex flex-col items-center gap-3">
        <span
          aria-hidden="true"
          className="size-8 animate-spin rounded-full border-2 border-zinc-700 border-t-brand-500"
        />
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    </div>
  );
}

/** Empty state with optional primary action. */
export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center gap-3 px-6 py-12 text-center">
      <div className="flex size-12 items-center justify-center rounded-full bg-zinc-800/80 text-zinc-400 ring-1 ring-zinc-700/60">
        {icon}
      </div>
      <div className="space-y-1">
        <p className="text-sm font-semibold text-zinc-100">{title}</p>
        <p className="mx-auto max-w-md text-sm leading-relaxed text-zinc-400">{description}</p>
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

/** Error state with retry. Message is the actual API error, never fabricated. */
export function ErrorState({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string | null;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="card border-red-500/30 bg-red-500/5 px-6 py-8 text-center"
    >
      <p className="text-sm font-semibold text-red-200">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-red-300/90">
        {message ?? "Something went wrong."}
      </p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-secondary mt-4">
          Try again
        </button>
      )}
    </div>
  );
}
