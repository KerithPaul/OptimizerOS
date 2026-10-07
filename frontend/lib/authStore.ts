"use client";

/**
 * Minimal client-side auth cache shared by every `useCurrentUser()` caller.
 *
 * The header lives in the root layout and mounts once; a client-side
 * `router.push` after login never re-mounts it, so without this cache the
 * profile avatar stayed invisible until a full page refresh. `login()` and
 * `logout()` (lib/api.ts) publish here, and all subscribers — header
 * included — re-render instantly.
 */

import type { User } from "./types";

let cachedUser: User | null = null;
const listeners = new Set<(user: User | null) => void>();

export function getCachedUser(): User | null {
  return cachedUser;
}

export function setCachedUser(user: User | null): void {
  cachedUser = user;
  for (const listener of listeners) listener(user);
}

export function subscribeUser(listener: (user: User | null) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
