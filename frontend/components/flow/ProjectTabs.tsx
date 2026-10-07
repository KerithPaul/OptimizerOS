"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS: { href: string; label: string }[] = [
  { href: "", label: "Overview" },
  { href: "/code", label: "Repository & Code" },
  { href: "/agents", label: "Improve" },
  { href: "/changes", label: "Changes" },
  { href: "/rollback", label: "Rollback" },
  { href: "/github", label: "GitHub" },
  { href: "/wordpress", label: "WordPress" },
  { href: "/search-console", label: "Search Console" },
  { href: "/reports", label: "Reports" },
  { href: "/experiments", label: "Experiments" },
];

/** Sticky contextual navigation across all project sub-pages. */
export function ProjectTabs({ projectId }: { projectId: number }) {
  const pathname = usePathname();
  const base = `/projects/${projectId}`;

  return (
    <nav
      aria-label="Project sections"
      className="theme-surface-bar sticky top-14 z-30 -mx-4 mb-8 border-b border-zinc-800/70 px-4 backdrop-blur-md sm:-mx-6 sm:px-6"
    >
      <ul className="flex gap-1 overflow-x-auto py-1.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {TABS.map((tab) => {
          const href = tab.href ? `${base}${tab.href}` : base;
          const active =
            tab.href === "" ? pathname === base : pathname.startsWith(`${base}${tab.href}`);
          return (
            <li key={tab.href} className="shrink-0">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={`inline-block whitespace-nowrap rounded-lg px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? "bg-brand-500/15 font-medium text-brand-300 ring-1 ring-inset ring-brand-500/30"
                    : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100"
                }`}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
