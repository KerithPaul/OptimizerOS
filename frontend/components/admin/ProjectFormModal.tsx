"use client";

/**
 * Create/Edit project modal. Create sends { name, mode } to the real API;
 * edit only changes the mode (the backend has no project rename endpoint),
 * so the name field is shown read-only while editing.
 */

import { useEffect, useState } from "react";
import type { ProjectMode } from "@/lib/types";
import { PROJECT_MODES } from "@/lib/types";
import { Modal } from "./adminui";

export const MODE_HINTS: Record<ProjectMode, string> = {
  AUDIT_ONLY: "Crawl and audit the website; propose fixes only.",
  SUGGEST_ONLY: "Audit plus detailed suggestions, applied by your team.",
  APPLY_LOCALLY: "Patch the attached git workspace, no commits.",
  COMMIT: "Commit changes to a local branch.",
  CREATE_PR: "Open pull requests with the proposed changes.",
};

export interface ProjectFormValues {
  name: string;
  mode: ProjectMode;
}

export function ProjectFormModal({
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
  initial?: ProjectFormValues;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (values: ProjectFormValues) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [projectMode, setProjectMode] = useState<ProjectMode>(initial?.mode ?? "AUDIT_ONLY");
  const [touched, setTouched] = useState(false);

  // Reset the form each time the modal opens.
  useEffect(() => {
    if (open) {
      setName(initial?.name ?? "");
      setProjectMode(initial?.mode ?? "AUDIT_ONLY");
      setTouched(false);
    }
  }, [open, initial]);

  const nameError =
    touched && !name.trim()
      ? "Project name is required."
      : touched && name.trim().length > 80
        ? "Project name must be 80 characters or fewer."
        : null;

  return (
    <Modal
      open={open}
      title={mode === "create" ? "Create project" : "Edit project"}
      description={
        mode === "create"
          ? "Each project tracks one website and its audit history, findings, and improvements."
          : "The project name is fixed after creation; you can change how changes are applied."
      }
      onClose={onClose}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          // Validate inline: `nameError` still reflects the pre-submit render.
          const trimmed = name.trim();
          if (trimmed.length === 0 || trimmed.length > 80) return;
          onSubmit({ name: trimmed, mode: projectMode });
        }}
        noValidate
      >
        <label className="field-label" htmlFor="admin-project-name">
          Project name
        </label>
        <input
          id="admin-project-name"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (touched) setTouched(false);
          }}
          onBlur={() => setTouched(true)}
          readOnly={mode === "edit"}
          disabled={pending}
          placeholder="e.g. Acme Marketing Site"
          className={`field-input mb-1 ${mode === "edit" ? "opacity-70" : ""}`}
          aria-invalid={nameError ? true : undefined}
          aria-describedby={nameError ? "admin-project-name-error" : undefined}
        />
        {nameError ? (
          <p id="admin-project-name-error" role="alert" className="field-error mb-3">
            {nameError}
          </p>
        ) : (
          <div className="mb-3" />
        )}

        <label className="field-label" htmlFor="admin-project-mode">
          Mode
        </label>
        <select
          id="admin-project-mode"
          value={projectMode}
          onChange={(e) => setProjectMode(e.target.value as ProjectMode)}
          disabled={pending}
          className="field-input mb-1.5"
        >
          {PROJECT_MODES.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <p className="field-hint mb-4">{MODE_HINTS[projectMode]}</p>

        {error && (
          <p role="alert" className="note-error mb-4 text-xs">
            {error}
          </p>
        )}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" onClick={onClose} disabled={pending} className="btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={pending || Boolean(nameError)} className="btn-primary">
            {pending ? (mode === "create" ? "Creating…" : "Saving…") : mode === "create" ? "Create project" : "Save changes"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
