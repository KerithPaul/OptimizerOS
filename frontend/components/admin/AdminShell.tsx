"use client";

/**
 * Admin layout: left sidebar + content area, reusing the app's shared
 * token classes (card, btn-*, badge, eyebrow) and the active-nav styling
 * from AppNav/ProjectTabs. Rendered inside the existing root layout, so
 * the global AppNav stays on top; the sidebar sits below it on desktop
 * and collapses into a horizontal tab bar on small screens, matching the
 * responsive pattern of ProjectTabs.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { FullPageLoader } from "@/components/flow/states";

const NAV_ITEMS = [
  { href: "/admin", label: "Dashboard", exact: true, icon: <DashboardIcon /> },
  { href: "/admin/projects", label: "Projects", exact: false, icon: <FolderIcon /> },
  { href: "/admin/users", label: "Users", exact: false, icon: <UsersIcon /> },
];

function DashboardIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
      <path
        d="M4 5.5A1.5 1.5 0 0 1 5.5 4h4A1.5 1.5 0 0 1 11 5.5v4A1.5 1.5 0 0 1 9.5 11h-4A1.5 1.5 0 0 1 4 9.5v-4ZM13 5.5A1.5 1.5 0 0 1 14.5 4h4A1.5 1.5 0 0 1 20 5.5v4a1.5 1.5 0 0 1-1.5 1.5h-4A1.5 1.5 0 0 1 13 9.5v-4ZM4 14.5A1.5 1.5 0 0 1 5.5 13h4a1.5 1.5 0 0 1 1.5 1.5v4A1.5 1.5 0 0 1 9.5 20h-4A1.5 1.5 0 0 1 4 18.5v-4ZM13 14.5a1.5 1.5 0 0 1 1.5-1.5h4a1.5 1.5 0 0 1 1.5 1.5v4a1.5 1.5 0 0 1-1.5 1.5h-4a1.5 1.5 0 0 1-1.5-1.5v-4Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
      <path
        d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function UsersIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
      <path
        d="M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1m18 0v-1a4 4 0 0 0-2.5-3.7M15 4.6a3.5 3.5 0 0 1 0 6.8M12 7.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function isActive(pathname: string, item: (typeof NAV_ITEMS)[number]) {
  return item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

const linkClass = (active: boolean) =>
  `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
    active
      ? "bg-brand-500/15 font-medium text-brand-300 ring-1 ring-inset ring-brand-500/30"
      : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100"
  }`;

export function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user, loading } = useCurrentUser();

  // Route guard: only admins may enter /admin. Members get a clean
  // not-authorized panel; signed-out users are redirected by the hook.
  if (loading) {
    return <FullPageLoader />;
  }
  if (!user || user.role !== "admin") {
    return (
      <div className="mx-auto w-full max-w-3xl flex-1 animate-fade-up px-4 py-16 sm:px-6">
        <div className="card p-10 text-center">
          <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-full bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30">
            <svg viewBox="0 0 24 24" fill="none" className="size-6" aria-hidden="true">
              <path
                d="M12 3 4 6.5v5.1c0 4.6 3.4 7.6 8 9.4 4.6-1.8 8-4.8 8-9.4V6.5L12 3Zm0 5.5v6M9 11.5h6"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <p className="text-sm font-semibold text-zinc-100">Admins only</p>
          <p className="mx-auto mt-1 max-w-sm text-sm leading-relaxed text-zinc-400">
            You don't have permission to view the admin dashboard.
          </p>
          <Link href="/projects" className="btn-primary mt-4">
            Back to projects
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl flex-1 animate-fade-up px-4 py-6 sm:px-6 sm:py-8">
      <div className="flex flex-col gap-6 lg:flex-row lg:gap-8">
        {/* Sidebar (desktop) */}
        <aside className="hidden w-52 shrink-0 lg:block">
          <div className="card sticky top-[4.5rem] p-3">
            <p className="eyebrow px-3 pb-2 pt-1.5">Admin</p>
            <nav aria-label="Admin" className="space-y-0.5">
              {NAV_ITEMS.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={isActive(pathname, item) ? "page" : undefined}
                  className={linkClass(isActive(pathname, item))}
                >
                  {item.icon}
                  {item.label}
                </Link>
              ))}
            </nav>
            <div className="mt-3 border-t border-zinc-800/70 pt-3 light:border-zinc-200">
              <Link
                href="/projects"
                className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-zinc-400 transition-colors hover:bg-zinc-800/60 hover:text-zinc-100"
              >
                <span aria-hidden="true">←</span> Back to app
              </Link>
            </div>
          </div>
        </aside>

        {/* Tab bar (mobile) — same pattern as ProjectTabs */}
        <nav
          aria-label="Admin"
          className="theme-surface-bar sticky top-14 z-30 -mx-4 border-b border-zinc-800/70 px-4 backdrop-blur-md sm:-mx-6 sm:px-6 lg:hidden"
        >
          <ul className="flex gap-1 overflow-x-auto py-1.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {NAV_ITEMS.map((item) => {
              const active = isActive(pathname, item);
              return (
                <li key={item.href} className="shrink-0">
                  <Link
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={`inline-block whitespace-nowrap rounded-lg px-3 py-1.5 text-sm transition-colors ${
                      active
                        ? "bg-brand-500/15 font-medium text-brand-300 ring-1 ring-inset ring-brand-500/30"
                        : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100"
                    }`}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </div>
  );
}
