import type { ReactNode } from "react";
import Link from "next/link";

/** One level of a breadcrumb trail. A href renders it as a link, otherwise text. */
export interface Crumb {
  label: string;
  href?: string;
}

export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="mb-3">
      <ol className="flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <li key={`${item.label}:${index}`} className="flex items-center gap-1.5">
              {item.href && !last ? (
                <Link href={item.href} className="transition-colors hover:text-brand-300">
                  {item.label}
                </Link>
              ) : (
                <span className={last ? "font-medium text-zinc-300" : ""}>{item.label}</span>
              )}
              {!last && <span aria-hidden="true">/</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** "← Back to overview" link shown at the top of project sub-pages. */
export function BackToOverview({ projectId }: { projectId: number }) {
  return (
    <Link
      href={`/projects/${projectId}`}
      className="mb-3 inline-flex items-center gap-1.5 text-sm font-medium text-zinc-400 transition-colors hover:text-brand-300"
    >
      <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
        <path
          d="M19 12H5m0 0 6 6m-6-6 6-6"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      Back to overview
    </Link>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-8 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-gradient">{title}</h1>
        {description && (
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-zinc-400">{description}</p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function SectionHeading({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">{title}</h2>
        {description && (
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-zinc-400">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/** Consistent page container: centered, responsive gutters. */
export function PageContainer({
  children,
  width = "3xl",
}: {
  children: ReactNode;
  width?: "sm" | "md" | "lg" | "xl" | "3xl" | "4xl" | "5xl" | "6xl";
}) {
  return (
    <div
      className={`mx-auto w-full max-w-${width} flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10`}
    >
      {children}
    </div>
  );
}

/** Title block used across project sub-pages. */
export function SubPageHeader({
  backHref,
  backLabel,
  title,
  description,
  actions,
}: {
  backHref: string;
  backLabel: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <>
      <Link
        href={backHref}
        className="mb-5 inline-flex items-center gap-1.5 text-sm text-zinc-500 transition-colors hover:text-brand-300"
      >
        <span aria-hidden="true">←</span> {backLabel}
      </Link>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {description && (
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-zinc-400">{description}</p>
          )}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </>
  );
}
