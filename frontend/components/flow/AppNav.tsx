"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { logout } from "@/lib/api";
import { useOptionalCurrentUser } from "@/lib/useCurrentUser";
import { ThemeToggle } from "@/components/flow/ThemeToggle";

const NAV_LINKS = [
  { href: "/projects", label: "Projects" },
  { href: "/docs", label: "Docs" },
];

export function AppNav() {
  const pathname = usePathname();
  // Non-redirecting variant: the header also renders on /login, where a
  // 401 must not bounce the visitor anywhere. Updates live via the shared
  // auth cache the instant login()/logout() publish.
  const { user } = useOptionalCurrentUser();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const closeMenu = useCallback((returnFocus = false) => {
    setMenuOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  // Close the menu on outside pointer press or Escape.
  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(e: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setMenuOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  async function handleLogout() {
    try {
      await logout();
    } finally {
      closeMenu();
      // Full navigation clears all client state after logout.
      window.location.href = "/login";
    }
  }

  return (
    <header className="theme-surface-bar sticky top-0 z-40 border-b border-zinc-800/70 backdrop-blur-xl light:border-[#2b5785]">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex items-center gap-6">
          <Link href="/projects" className="group flex items-center gap-2" aria-label="ArchitectOS home">
            <span className="bg-grid flex size-7 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-accent-600 shadow-[0_4px_16px_-4px_rgb(99_102_241/0.6)] transition group-hover:shadow-[0_4px_20px_-4px_rgb(99_102_241/0.9)]">
              <svg viewBox="0 0 24 24" fill="none" className="size-4 text-white" aria-hidden="true">
                <path
                  d="M4 20h16M6 20V9.5L12 5l6 4.5V20M10 20v-5h4v5"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <span className="text-sm font-semibold tracking-tight text-zinc-100">
              Architect<span className="text-brand-400">OS</span>
            </span>
          </Link>
          <nav aria-label="Main" className="flex items-center gap-1">
            {NAV_LINKS.map((link) => {
              const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded-lg px-2.5 py-1 text-sm transition-colors ${
                    active
                      ? "bg-brand-500/15 font-medium text-brand-300 ring-1 ring-inset ring-brand-500/30"
                      : "text-zinc-400 hover:bg-zinc-800/70 hover:text-zinc-100"
                  }`}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="flex items-center gap-1.5">
          <ThemeToggle />

        {user && (
          <div ref={menuRef} className="relative flex items-center">
            <button
              ref={triggerRef}
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label={`Account menu for ${user.email}`}
              className="flex items-center gap-2 rounded-full outline-none transition focus-visible:ring-2 focus-visible:ring-brand-400"
            >
              <span
                aria-hidden="true"
                className="flex size-8 items-center justify-center rounded-full bg-gradient-to-br from-brand-500/80 to-accent-500/80 text-[11px] font-bold uppercase text-white ring-2 ring-zinc-800 ring-inset transition hover:ring-brand-500/60 light:ring-[#2b5785]"
              >
                {user.email.slice(0, 2)}
              </span>
            </button>

            {menuOpen && (
              <div
                role="menu"
                aria-label="Account"
                className="animate-fade-up absolute right-0 top-11 z-50 w-60 overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900 shadow-[0_16px_48px_-16px_rgb(0_0_0/0.9)] light:border-[#d9e2ec] light:bg-white light:shadow-[0_16px_48px_-16px_rgb(18_59_109/0.35)]"
              >
                <div className="border-b border-zinc-800/80 px-4 py-3">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                    Signed in as
                  </p>
                  <p className="mt-0.5 truncate text-sm font-medium text-zinc-100" title={user.email}>
                    {user.email}
                  </p>
                </div>
                <div className="p-1.5">
                  <Link
                    href="/projects"
                    role="menuitem"
                    onClick={() => closeMenu()}
                    className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800/80 hover:text-zinc-100"
                  >
                    <svg viewBox="0 0 24 24" fill="none" className="size-4 text-zinc-500" aria-hidden="true">
                      <path
                        d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    Projects
                  </Link>
                  {user?.role === "admin" && (
                    <Link
                      href="/admin"
                      role="menuitem"
                      onClick={() => closeMenu()}
                      className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800/80 hover:text-zinc-100"
                    >
                      <svg viewBox="0 0 24 24" fill="none" className="size-4 text-zinc-500" aria-hidden="true">
                        <path
                          d="M12 3 4 6.5v5.1c0 4.6 3.4 7.6 8 9.4 4.6-1.8 8-4.8 8-9.4V6.5L12 3Zm0 5.5v6M9 11.5h6"
                          stroke="currentColor"
                          strokeWidth="1.6"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                      Admin dashboard
                    </Link>
                  )}
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => void handleLogout()}
                    className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-red-300 transition-colors hover:bg-red-500/10"
                  >
                    <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
                      <path
                        d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H3"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    Log out
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
        </div>
      </div>
    </header>
  );
}
