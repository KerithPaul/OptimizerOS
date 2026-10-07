"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, createProject, deleteProject, listProjects } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { SectionHeading } from "@/components/flow/ui";
import { ListSkeleton } from "@/components/flow/states";
import { PROJECT_MODES, type Project, type ProjectMode } from "@/lib/types";

const MODE_HINTS: Record<ProjectMode, string> = {
  AUDIT_ONLY: "Crawl and audit the website; propose fixes only.",
  SUGGEST_ONLY: "Audit plus detailed suggestions, applied by your team.",
  APPLY_LOCALLY: "Patch the attached git workspace, no commits.",
  COMMIT: "Commit changes to a local branch.",
  CREATE_PR: "Open pull requests with the proposed changes.",
};

export default function ProjectsPage() {
  const router = useRouter();
  const { user, loading: userLoading } = useCurrentUser();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [name, setName] = useState("");
  const [mode, setMode] = useState<ProjectMode>("AUDIT_ONLY");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!user) return;
    listProjects()
      .then(setProjects)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load projects."))
      .finally(() => setLoadingProjects(false));
  }, [user]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const project = await createProject(name.trim(), mode);
      router.push(`/projects/${project.id}?created=1`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create project.");
      setSubmitting(false);
    }
  }

  /* ---------------- delete flow (real DELETE /projects/{id} API) -------- */

  const [pendingDelete, setPendingDelete] = useState<Project | null>(null);
  const [confirmName, setConfirmName] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const deleteInputRef = useRef<HTMLInputElement | null>(null);

  // Reset the confirmation input each time the dialog opens.
  useEffect(() => {
    if (pendingDelete) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reset dialog state on open
      setConfirmName("");
      setDeleteError(null);
      deleteInputRef.current?.focus();
    }
  }, [pendingDelete]);

  // Close the dialog on Escape.
  useEffect(() => {
    if (!pendingDelete) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && !deleting) setPendingDelete(null);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [pendingDelete, deleting]);

  async function handleDeleteConfirmed() {
    if (!pendingDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteProject(pendingDelete.id);
      setProjects((rows) => rows.filter((row) => row.id !== pendingDelete.id));
      setPendingDelete(null);
    } catch (err) {
      setDeleteError(
        err instanceof ApiError ? err.message : "Failed to delete the project. Try again."
      );
    } finally {
      setDeleting(false);
    }
  }

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  const nameMatches = pendingDelete !== null && confirmName.trim() === pendingDelete.name;

  return (
    <div className="mx-auto w-full max-w-3xl flex-1 animate-fade-up px-4 py-10 sm:px-6">
      {/* Create project ---------------------------------------------------- */}
      <section className="mb-12">
        <SectionHeading
          title="Create a project"
          description="Each project tracks one website and its audit history, findings, and improvements."
        />
        <form onSubmit={handleCreate} className="card p-6" noValidate>
          <label className="field-label" htmlFor="name">
            Project name
          </label>
          <input
            id="name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Acme Marketing Site"
            className="field-input mb-4"
          />

          <label className="field-label" htmlFor="mode">
            Mode
          </label>
          <select
            id="mode"
            value={mode}
            onChange={(e) => setMode(e.target.value as ProjectMode)}
            className="field-input mb-1.5"
          >
            {PROJECT_MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <p className="field-hint mb-4">{MODE_HINTS[mode]}</p>

          {error && (
            <p role="alert" className="note-error mb-4">
              {error}
            </p>
          )}

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <button type="submit" disabled={submitting} className="btn-primary">
              {submitting ? "Creating…" : "Create project"}
            </button>
            <p className="field-hint mt-0 sm:mt-0">
              Next step after creating: enter your website URL to generate an audit.
            </p>
          </div>
        </form>
      </section>

      {/* Project list ------------------------------------------------------ */}
      <section>
        <SectionHeading
          title={`Your projects${projects.length ? ` (${projects.length})` : ""}`}
        />
        {loadingProjects ? (
          <ListSkeleton rows={3} />
        ) : projects.length === 0 ? (
          <div className="card p-10 text-center">
            <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-full bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30">
              <svg viewBox="0 0 24 24" fill="none" className="size-6" aria-hidden="true">
                <path
                  d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <p className="text-sm font-semibold text-zinc-100">No projects yet</p>
            <p className="mx-auto mt-1 max-w-sm text-sm leading-relaxed text-zinc-400">
              Create your first project above to start auditing and improving your website.
            </p>
          </div>
        ) : (
          <ul className="space-y-3">
            {projects.map((project) => (
              <li key={project.id} className="card card-hover group flex items-center gap-4 p-5">
                <Link href={`/projects/${project.id}`} className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-zinc-100">{project.name}</span>
                  <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                    <span className="badge bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30">
                      {project.mode.toLowerCase().replace(/_/g, " ")}
                    </span>
                    <span>Created {new Date(project.created_at).toLocaleDateString()}</span>
                  </span>
                </Link>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setPendingDelete(project)}
                    aria-label={`Delete project ${project.name}`}
                    title="Delete project"
                    className="btn-ghost px-2 text-zinc-500 hover:bg-red-500/10 hover:text-red-300"
                  >
                    <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
                      <path
                        d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m3 0v12a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V7m5 4v6m4-6v6"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                  <span
                    aria-hidden="true"
                    className="text-zinc-600 transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-brand-300"
                  >
                    →
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Delete confirmation dialog ----------------------------------------- */}
      {pendingDelete && (
        <div
          className="fixed inset-0 z-50 flex items-end justify-center bg-zinc-950/40 p-4 backdrop-blur-sm sm:items-center light:bg-[#0d2f57]/30"
          role="presentation"
          onClick={() => {
            if (!deleting) setPendingDelete(null);
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-dialog-title"
            aria-describedby="delete-dialog-description"
            className="card w-full max-w-md animate-fade-up p-6"
            onClick={(e) => e.stopPropagation()}
          >
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
            <h2 id="delete-dialog-title" className="text-base font-semibold text-zinc-100">
              Delete project
            </h2>
            <p id="delete-dialog-description" className="mt-1.5 text-sm leading-relaxed text-zinc-400">
              Permanently delete{" "}
              <span className="font-medium text-zinc-200">{pendingDelete.name}</span> and all of
              its data — website, audits, findings, and changes. This cannot be undone.
            </p>

            <label className="field-label mt-5" htmlFor="delete-confirm-name">
              Type <span className="font-semibold text-zinc-200">{pendingDelete.name}</span> to
              confirm
            </label>
            <input
              ref={deleteInputRef}
              id="delete-confirm-name"
              value={confirmName}
              onChange={(e) => {
                setConfirmName(e.target.value);
                if (deleteError) setDeleteError(null);
              }}
              className="field-input mb-4"
              autoComplete="off"
              aria-describedby={deleteError ? "delete-confirm-error" : undefined}
              aria-invalid={deleteError ? true : undefined}
            />

            {deleteError && (
              <p id="delete-confirm-error" role="alert" className="note-error mb-4 text-xs">
                {deleteError}
              </p>
            )}

            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={() => setPendingDelete(null)}
                disabled={deleting}
                className="btn-secondary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteConfirmed()}
                disabled={deleting || !nameMatches}
                className="btn-danger"
              >
                {deleting ? "Deleting…" : "Delete project"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
