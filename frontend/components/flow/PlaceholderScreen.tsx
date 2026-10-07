import Link from "next/link";
import { PageHeader } from "@/components/flow/ui";

export function PlaceholderScreen({
  activeItem,
  title,
  heading,
  description,
  ctaLabel,
}: {
  activeItem: string;
  title: string;
  crumbs?: string[];
  heading: string;
  description: string;
  ctaLabel?: string;
}) {
  void activeItem;
  return (
    <div className="mx-auto w-full max-w-2xl flex-1 animate-fade-up px-4 py-16 sm:px-6">
      <PageHeader title={title} />
      <div className="card p-8 text-center">
        <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-full bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30">
          <svg viewBox="0 0 24 24" fill="none" className="size-6" aria-hidden="true">
            <path
              d="M12 6.5v5l3 2M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18Z"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <h2 className="text-base font-semibold text-zinc-100">{heading}</h2>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-zinc-400">{description}</p>
        {ctaLabel && (
          <Link href="/projects" className="btn-primary mt-6">
            {ctaLabel}
          </Link>
        )}
      </div>
    </div>
  );
}
