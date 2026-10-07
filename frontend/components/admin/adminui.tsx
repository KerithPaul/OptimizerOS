"use client";

/**
 * Reusable admin building blocks, composed from the app's existing token
 * classes (.card, .btn-*, .field-*, .badge) so the admin area inherits the
 * exact visual language — nothing new is introduced here structurally.
 */

import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";

/* -------------------------------------------------------------------------- */
/* StatCard — dashboard metric tile; grows with future stats via `hint`.       */
/* -------------------------------------------------------------------------- */

export function StatCard({
  label,
  value,
  hint,
  loading = false,
  icon,
}: {
  label: string;
  value: number | string | null;
  hint?: string;
  loading?: boolean;
  icon?: ReactNode;
}) {
  return (
    <div className="card card-hover p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="eyebrow">{label}</p>
        {icon && (
          <span
            aria-hidden="true"
            className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30"
          >
            {icon}
          </span>
        )}
      </div>
      {loading ? (
        <div className="skeleton mt-2 h-9 w-16" />
      ) : (
        <p className="mt-1.5 text-3xl font-semibold tracking-tight text-zinc-100">
          {value ?? "—"}
        </p>
      )}
      {hint && !loading && <p className="mt-1 text-xs text-zinc-500">{hint}</p>}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Modal — overlay + dialog matching the app's delete-confirmation styling.   */
/* -------------------------------------------------------------------------- */

export function Modal({
  open,
  title,
  description,
  onClose,
  children,
  width = "max-w-md",
}: {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  width?: "max-w-md" | "max-w-lg";
}) {
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-zinc-950/40 p-4 backdrop-blur-sm sm:items-center light:bg-[#0d2f57]/30"
      role="presentation"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        className={`card w-full ${width} animate-fade-up p-6`}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id={titleId} className="text-base font-semibold text-zinc-100">
          {title}
        </h2>
        {description && (
          <p id={descriptionId} className="mt-1.5 text-sm leading-relaxed text-zinc-400">
            {description}
          </p>
        )}
        <div className="mt-5">{children}</div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* ConfirmDialog — destructive-action confirmation (danger icon + buttons).   */
/* -------------------------------------------------------------------------- */

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  pendingLabel,
  pending = false,
  error = null,
  confirmKeyword,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  pendingLabel?: string;
  pending?: boolean;
  error?: string | null;
  /** When set, the user must type this exact value to enable the confirm button. */
  confirmKeyword?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (open) {
      setTyped("");
      inputRef.current?.focus();
    }
  }, [open]);

  const confirmed = !confirmKeyword || typed.trim() === confirmKeyword;

  return (
    <Modal open={open} title={title} onClose={onCancel}>
      <div className="mb-4 flex size-11 items-center justify-center rounded-full bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30">
        <svg viewBox="0 0 24 24" fill="none" className="size-5" aria-hidden="true">
          <path
            d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m3 0v12a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V7m5 4v6m4-6v6"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <p className="text-sm leading-relaxed text-zinc-400">{description}</p>
      {confirmKeyword && (
        <>
          <label className="field-label mt-5" htmlFor="admin-confirm-keyword">
            Type <span className="font-semibold text-zinc-200">{confirmKeyword}</span> to confirm
          </label>
          <input
            ref={inputRef}
            id="admin-confirm-keyword"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            disabled={pending}
            className="field-input"
            autoComplete="off"
          />
        </>
      )}
      {error && (
        <p role="alert" className="note-error mt-4 text-xs">
          {error}
        </p>
      )}
      <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onCancel} disabled={pending} className="btn-secondary">
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={pending || !confirmed}
          className="btn-danger"
        >
          {pending ? (pendingLabel ?? "Deleting…") : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}

/* -------------------------------------------------------------------------- */
/* DataTable — semantic table shell with the app's dividers and hover rows.   */
/* -------------------------------------------------------------------------- */

export function DataTable({
  columns,
  children,
  footer,
}: {
  columns: string[];
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-800/70 light:border-zinc-200">
              {columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className="px-4 py-3 text-xs font-semibold uppercase tracking-widest text-zinc-500"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70 light:divide-zinc-200">{children}</tbody>
        </table>
      </div>
      {footer && (
        <div className="border-t border-zinc-800/70 px-4 py-3 light:border-zinc-200">{footer}</div>
      )}
    </div>
  );
}

export function TableRow({ children }: { children: ReactNode }) {
  return (
    <tr className="transition-colors hover:bg-zinc-800/40 light:hover:bg-zinc-50">{children}</tr>
  );
}

export function TableCell({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <td className={`px-4 py-3.5 align-middle ${className}`}>{children}</td>;
}

/* -------------------------------------------------------------------------- */
/* SearchInput — debounced filter box used above tables.                      */
/* -------------------------------------------------------------------------- */

export function SearchInput({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label: string;
}) {
  const [immediate, setImmediate] = useState(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function handle(next: string) {
    setImmediate(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => onChange(next), 200);
  }

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  return (
    <div className="relative">
      <label className="sr-only" htmlFor={`search-${label}`}>
        {label}
      </label>
      <svg
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
        className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-zinc-500"
      >
        <path
          d="m21 21-4.35-4.35M17 11a6 6 0 1 1-12 0 6 6 0 0 1 12 0Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
      <input
        id={`search-${label}`}
        type="search"
        value={immediate}
        onChange={(e) => handle(e.target.value)}
        placeholder={placeholder}
        className="field-input pl-9"
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* StatusBadge / RoleBadge — pill styles from the app's badge vocabulary.     */
/* -------------------------------------------------------------------------- */

const STATUS_STYLES = {
  active: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  suspended: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
} as const;

export function StatusBadge({ status }: { status: "active" | "suspended" }) {
  return <span className={`badge ${STATUS_STYLES[status]}`}>{status}</span>;
}

const ROLE_STYLES = {
  admin: "bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30",
  member: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
} as const;

export function RoleBadge({ role }: { role: "admin" | "member" }) {
  return <span className={`badge ${ROLE_STYLES[role]}`}>{role}</span>;
}

const MODE_STYLES = "bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/30";

export function ModeBadge({ mode }: { mode: string }) {
  return <span className={`badge ${MODE_STYLES}`}>{mode.toLowerCase().replace(/_/g, " ")}</span>;
}

/* -------------------------------------------------------------------------- */
/* Avatar — initials circle, same gradient as AppNav's account avatar.        */
/* -------------------------------------------------------------------------- */

export function Avatar({ name, email }: { name?: string; email: string }) {
  const initials = (name || email).slice(0, 2);
  return (
    <span
      aria-hidden="true"
      className="flex size-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-500/80 to-accent-500/80 text-[11px] font-bold uppercase text-white ring-2 ring-zinc-800 ring-inset light:ring-[#2b5785]"
    >
      {initials}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* IconAction — small ghost icon button for row actions.                      */
/* -------------------------------------------------------------------------- */

export function IconAction({
  label,
  onClick,
  tone = "default",
  children,
  disabled = false,
}: {
  label: string;
  onClick: () => void;
  tone?: "default" | "danger";
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={`btn-ghost px-2 disabled:cursor-not-allowed disabled:opacity-40 ${
        tone === "danger" ? "hover:bg-red-500/10 hover:text-red-300" : "hover:text-zinc-100"
      }`}
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* formatDate — shared created-date rendering.                                */
/* -------------------------------------------------------------------------- */

export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
