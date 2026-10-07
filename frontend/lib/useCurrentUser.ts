"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, getCurrentUser } from "./api";
import { getCachedUser, setCachedUser, subscribeUser } from "./authStore";
import type { User } from "./types";

/**
 * Current user for the calling component.
 *
 * Reads the shared auth cache first (so a login elsewhere in the app shows
 * instantly, without a refresh), then verifies against /auth/me. Redirects
 * to /login when there is no valid session.
 */
export function useCurrentUser(): { user: User | null; loading: boolean } {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(getCachedUser);
  const [loading, setLoading] = useState<boolean>(getCachedUser() === null);

  useEffect(() => {
    // Live updates when login()/logout() publish a new session state.
    const unsubscribe = subscribeUser(setUser);
    return unsubscribe;
  }, []);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser()
      .then((u) => {
        if (!cancelled) setCachedUser(u);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          // Only clear the cache for pages that need auth; the login page
          // itself uses a lighter hook (see useOptionalCurrentUser).
          if (router && !window.location.pathname.startsWith("/login")) {
            setCachedUser(null);
            router.replace("/login");
          }
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  return { user, loading };
}

/**
 * Same fetch, but never redirects and tolerates logged-out state — used on
 * public pages (login) and by the header.
 */
export function useOptionalCurrentUser(): { user: User | null; loading: boolean } {
  const [user, setUser] = useState<User | null>(getCachedUser);
  const [loading, setLoading] = useState<boolean>(getCachedUser() === null);

  useEffect(() => {
    const unsubscribe = subscribeUser(setUser);
    return unsubscribe;
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (getCachedUser()) {
      setLoading(false);
      return;
    }
    getCurrentUser()
      .then((u) => {
        if (!cancelled) setCachedUser(u);
      })
      .catch(() => {
        if (!cancelled) setCachedUser(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { user, loading };
}
