"use client";

import { useCallback, useSyncExternalStore } from "react";

type Theme = "dark" | "light";

/* The source of truth is the class on <html>, applied pre-paint by the inline
 * head script. useSyncExternalStore keeps the button in sync without effects
 * or hydration mismatches (server snapshot renders the dark default). */
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): Theme {
  return document.documentElement.classList.contains("light") ? "light" : "dark";
}

function getServerSnapshot(): Theme {
  return "dark";
}

/** Sun/moon toggle. Persists the choice to localStorage; the head script
 * re-applies it before first paint on every load. */
export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const toggle = useCallback(() => {
    const root = document.documentElement;
    const next: Theme = root.classList.contains("light") ? "dark" : "light";
    root.classList.toggle("dark", next === "dark");
    root.classList.toggle("light", next === "light");
    try {
      localStorage.setItem("theme", next);
    } catch {
      // Private mode etc. — theme just won't persist.
    }
    listeners.forEach((notify) => notify());
  }, []);

  const isLight = theme === "light";
  const label = isLight ? "Switch to dark mode" : "Switch to light mode";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      title={label}
      aria-pressed={isLight}
      className="inline-flex size-8 items-center justify-center rounded-lg text-zinc-400 outline-none transition-colors hover:bg-zinc-800/70 hover:text-zinc-100 focus-visible:ring-2 focus-visible:ring-brand-400"
    >
      {isLight ? (
        <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
          <path
            d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
          <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.7" />
          <path
            d="M12 3v2m0 14v2m9-9h-2M5 12H3m13.5-6.5-1.5 1.5m-7 7-1.5 1.5m11 0-1.5-1.5m-7-7L5 5"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        </svg>
      )}
    </button>
  );
}
