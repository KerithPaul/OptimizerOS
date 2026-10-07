"use client";

/**
 * Create/Edit user modal. The user-management API does not exist yet, so
 * submission is handed to the page through `onSubmit`; the same modal serves
 * both flows with role/status fields and client-side validation.
 */

import { useEffect, useState } from "react";
import { Modal } from "./adminui";
import type { AdminUser } from "@/lib/admin";

export interface UserFormValues {
  name: string;
  email: string;
  role: AdminUser["role"];
  status: AdminUser["status"];
  /** Max projects a member may create; null = unlimited. */
  projectLimit: number | null;
  /** Set on create (required) and on edit when resetting the password. */
  password?: string;
}

const PROJECT_LIMIT_OPTIONS: (number | "")[] = ["", 1, 2, 3, 4, 5];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export function UserFormModal({
  open,
  mode, // "create" | "edit"
  initial,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  open: boolean;
  mode: "create" | "edit";
  initial?: UserFormValues;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (values: UserFormValues) => void;
}) {
  /** Options shown in the "Projects this member can create" select. */
  const limitOptions = PROJECT_LIMIT_OPTIONS;
  const [name, setName] = useState(initial?.name ?? "");
  const [email, setEmail] = useState(initial?.email ?? "");
  const [role, setRole] = useState<AdminUser["role"]>(initial?.role ?? "member");
  const [status, setStatus] = useState<AdminUser["status"]>(initial?.status ?? "active");
  const [projectLimit, setProjectLimit] = useState<number | "">(
    initial?.projectLimit ?? 1,
  );
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (open) {
      setName(initial?.name ?? "");
      setEmail(initial?.email ?? "");
      setRole(initial?.role ?? "member");
      setStatus(initial?.status ?? "active");
      setProjectLimit(initial?.projectLimit ?? 1);
      setPassword("");
      setTouched(false);
    }
  }, [open, initial]);

  const nameError = touched && !name.trim() ? "Name is required." : null;
  const emailError =
    touched && !email.trim()
      ? "Email is required."
      : touched && !EMAIL_RE.test(email.trim())
        ? "Enter a valid email address."
        : null;
  const passwordError =
    touched && mode === "create" && password.length < 8
      ? "Password must be at least 8 characters."
      : touched && mode === "edit" && password.length > 0 && password.length < 8
        ? "New password must be at least 8 characters."
        : null;
  const invalid = Boolean(nameError || emailError || passwordError);

  return (
    <Modal
      open={open}
      title={mode === "create" ? "Add user" : "Edit user"}
      description={
        mode === "create"
          ? "Create an account for a teammate. They will sign in with this email."
          : "Update this user's profile, role, and account status."
      }
      onClose={onClose}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          // Validate inline: nameError/emailError still reflect the
          // pre-submit render, so re-check the raw values here.
          const trimmedName = name.trim();
          const trimmedEmail = email.trim();
          if (!trimmedName || !EMAIL_RE.test(trimmedEmail)) return;
          if (mode === "create" && password.length < 8) return;
          if (mode === "edit" && password.length > 0 && password.length < 8) return;
          const limit = projectLimit === "" ? null : Number(projectLimit);
          onSubmit({
            name: trimmedName,
            email: trimmedEmail,
            role,
            status,
            // Admins are always unlimited; only members carry a limit.
            projectLimit: role === "admin" ? null : limit,
            ...(password ? { password } : {}),
          });
        }}
        noValidate
      >
        <label className="field-label" htmlFor="admin-user-name">
          Name
        </label>
        <input
          id="admin-user-name"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (touched) setTouched(false);
          }}
          onBlur={() => setTouched(true)}
          disabled={pending}
          placeholder="e.g. John Carter"
          className="field-input mb-1"
          aria-invalid={nameError ? true : undefined}
          aria-describedby={nameError ? "admin-user-name-error" : undefined}
        />
        {nameError ? (
          <p id="admin-user-name-error" role="alert" className="field-error mb-3">
            {nameError}
          </p>
        ) : (
          <div className="mb-3" />
        )}

        <label className="field-label" htmlFor="admin-user-email">
          Email
        </label>
        <input
          id="admin-user-email"
          type="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            if (touched) setTouched(false);
          }}
          onBlur={() => setTouched(true)}
          disabled={pending}
          placeholder="e.g. john@company.com"
          className="field-input mb-1"
          aria-invalid={emailError ? true : undefined}
          aria-describedby={emailError ? "admin-user-email-error" : undefined}
        />
        {emailError ? (
          <p id="admin-user-email-error" role="alert" className="field-error mb-3">
            {emailError}
          </p>
        ) : (
          <div className="mb-3" />
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="field-label" htmlFor="admin-user-role">
              Role
            </label>
            <select
              id="admin-user-role"
              value={role}
              onChange={(e) => setRole(e.target.value as AdminUser["role"])}
              disabled={pending}
              className="field-input"
            >
              <option value="member">member</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <div>
            <label className="field-label" htmlFor="admin-user-status">
              Status
            </label>
            <select
              id="admin-user-status"
              value={status}
              onChange={(e) => setStatus(e.target.value as AdminUser["status"])}
              disabled={pending}
              className="field-input"
            >
              <option value="active">active</option>
              <option value="suspended">suspended</option>
            </select>
          </div>
        </div>

        {role === "member" && (
          <div className="mt-4">
            <label className="field-label" htmlFor="admin-user-project-limit">
              Projects this member can create
            </label>
            <select
              id="admin-user-project-limit"
              value={String(projectLimit)}
              onChange={(e) =>
                setProjectLimit(e.target.value === "" ? "" : Number(e.target.value))
              }
              disabled={pending}
              className="field-input"
            >
              {limitOptions.map((option) => (
                <option key={String(option)} value={String(option)}>
                  {option === "" ? "Unlimited" : option === 1 ? "1 project" : `${option} projects`}
                </option>
              ))}
            </select>
            <p className="mt-1.5 text-xs text-zinc-500">
              The member will only see the projects they create themselves.
            </p>
          </div>
        )}

        <label className="field-label mt-4" htmlFor="admin-user-password">
          {mode === "create" ? "Password" : "New password (optional)"}
        </label>
        <input
          id="admin-user-password"
          type="password"
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            if (touched) setTouched(false);
          }}
          onBlur={() => setTouched(true)}
          disabled={pending}
          placeholder={mode === "create" ? "At least 8 characters" : "Leave blank to keep current"}
          className="field-input mb-1"
          autoComplete="new-password"
          aria-invalid={passwordError ? true : undefined}
          aria-describedby={passwordError ? "admin-user-password-error" : undefined}
        />
        {passwordError ? (
          <p id="admin-user-password-error" role="alert" className="field-error mb-3">
            {passwordError}
          </p>
        ) : (
          <div className="mb-3" />
        )}

        {error && (
          <p role="alert" className="note-error mt-4 text-xs">
            {error}
          </p>
        )}

        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" onClick={onClose} disabled={pending} className="btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={pending || invalid} className="btn-primary">
            {pending ? (mode === "create" ? "Creating…" : "Saving…") : mode === "create" ? "Add user" : "Save changes"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
