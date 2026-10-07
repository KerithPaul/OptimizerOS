"use client";

/**
 * Admin Projects management: table with full CRUD. Create/update/delete
 * call the real project endpoints; edit only changes mode (no rename API).
 * Search filters client-side over name and ID.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  adminCreateProject,
  adminDeleteProject,
  adminListProjects,
  adminListUsers,
  adminUpdateProjectMode,
} from "@/lib/admin";
import { ApiError } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { PageHeader } from "@/components/flow/ui";
import { ErrorState, ListSkeleton } from "@/components/flow/states";
import {
  ConfirmDialog,
  DataTable,
  IconAction,
  ModeBadge,
  SearchInput,
  TableCell,
  TableRow,
  formatDate,
} from "@/components/admin/adminui";
import {
  ProjectFormModal,
  type ProjectFormValues,
} from "@/components/admin/ProjectFormModal";
import type { Project } from "@/lib/types";

type PendingDelete = Project | null;

export default function AdminProjectsPage() {
  const { user, loading: userLoading } = useCurrentUser();
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [ownerNames, setOwnerNames] = useState<Map<number, string>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  // create / edit modal state
  const [formOpen, setFormOpen] = useState(false);
  const [formMode, setFormMode] = useState<"create" | "edit">("create");
  const [editing, setEditing] = useState<Project | null>(null);
  const [formPending, setFormPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // delete dialog state
  const [pendingDelete, setPendingDelete] = useState<PendingDelete>(null);
  const [deletePending, setDeletePending] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([adminListProjects(), adminListUsers().catch(() => null)])
      .then(([projectRows, userRows]) => {
        setProjects(projectRows);
        const owners = new Map<number, string>();
        for (const u of userRows ?? []) owners.set(u.id, u.name || u.email);
        setOwnerNames(owners);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Failed to load projects."),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!user) return;
    load();
  }, [user, load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return projects;
    return projects.filter(
      (p) => p.name.toLowerCase().includes(q) || String(p.id).includes(q),
    );
  }, [projects, query]);

  async function handleCreate(values: ProjectFormValues) {
    setFormPending(true);
    setFormError(null);
    try {
      await adminCreateProject(values.name, values.mode);
      setFormOpen(false);
      load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create the project.");
    } finally {
      setFormPending(false);
    }
  }

  async function handleEdit(values: ProjectFormValues) {
    if (!editing) return;
    setFormPending(true);
    setFormError(null);
    try {
      await adminUpdateProjectMode(editing.id, values.mode);
      setFormOpen(false);
      setEditing(null);
      load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to update the project.");
    } finally {
      setFormPending(false);
    }
  }

  async function handleDeleteConfirmed() {
    if (!pendingDelete) return;
    setDeletePending(true);
    setDeleteError(null);
    try {
      await adminDeleteProject(pendingDelete.id);
      setPendingDelete(null);
      load();
    } catch (err) {
      setDeleteError(
        err instanceof ApiError ? err.message : "Failed to delete the project. Try again.",
      );
    } finally {
      setDeletePending(false);
    }
  }

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  const ownerLabel = (createdBy: number) => ownerNames.get(createdBy) ?? `user #${createdBy}`;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Projects"
        description="All projects in the system. Create, adjust how changes are applied, or remove projects."
        actions={
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              setFormMode("create");
              setEditing(null);
              setFormError(null);
              setFormOpen(true);
            }}
          >
            + Add project
          </button>
        }
      />

      {loading ? (
        <ListSkeleton rows={5} />
      ) : error ? (
        <ErrorState title="Couldn't load projects" message={error} onRetry={load} />
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
            Create the first project to start tracking a website's audits and improvements.
          </p>
          <button
            type="button"
            className="btn-primary mt-4"
            onClick={() => {
              setFormMode("create");
              setEditing(null);
              setFormError(null);
              setFormOpen(true);
            }}
          >
            + Add project
          </button>
        </div>
      ) : (
        <>
          <SearchInput
            label="projects"
            value={query}
            onChange={setQuery}
            placeholder="Search by name or ID…"
          />

          {filtered.length === 0 ? (
            <div className="card p-10 text-center">
              <p className="text-sm font-semibold text-zinc-100">No matches</p>
              <p className="mx-auto mt-1 max-w-sm text-sm leading-relaxed text-zinc-400">
                No projects match “{query.trim()}”. Try a different name or ID.
              </p>
            </div>
          ) : (
            <DataTable
              columns={["Project", "Owner", "Created", "Mode", "Actions"]}
              footer={
                <p className="text-xs text-zinc-500">
                  {filtered.length} of {projects.length} project
                  {projects.length === 1 ? "" : "s"}
                </p>
              }
            >
              {filtered.map((project) => (
                <TableRow key={project.id}>
                  <TableCell>
                    <Link
                      href={`/projects/${project.id}`}
                      className="font-medium text-zinc-100 transition-colors hover:text-brand-300"
                    >
                      {project.name}
                    </Link>
                    <span className="mt-0.5 block font-mono text-xs text-zinc-500">
                      #{project.id}
                    </span>
                  </TableCell>
                  <TableCell className="text-zinc-400">{ownerLabel(project.created_by)}</TableCell>
                  <TableCell className="whitespace-nowrap text-zinc-400">
                    {formatDate(project.created_at)}
                  </TableCell>
                  <TableCell>
                    <ModeBadge mode={project.mode} />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-0.5">
                      <IconAction
                        label={`Open project ${project.name}`}
                        onClick={() => router.push(`/projects/${project.id}`)}
                      >
                        <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
                          <path
                            d="M14 4h6v6M20 4l-9 9M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"
                            stroke="currentColor"
                            strokeWidth="1.6"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </IconAction>
                      <IconAction
                        label={`Edit project ${project.name}`}
                        onClick={() => {
                          setEditing(project);
                          setFormMode("edit");
                          setFormError(null);
                          setFormOpen(true);
                        }}
                      >
                        <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
                          <path
                            d="m14.5 5.5 4 4M4 20h4.5l10-10a2.1 2.1 0 0 0-3-3l-10 10L4 20Z"
                            stroke="currentColor"
                            strokeWidth="1.6"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </IconAction>
                      <IconAction
                        label={`Delete project ${project.name}`}
                        tone="danger"
                        onClick={() => {
                          setPendingDelete(project);
                          setDeleteError(null);
                        }}
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
                      </IconAction>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </DataTable>
          )}
        </>
      )}

      {/* Create / Edit modal ------------------------------------------------- */}
      {formOpen && (
        <ProjectFormModal
          open={formOpen}
          mode={formMode}
          initial={
            formMode === "edit" && editing
              ? { name: editing.name, mode: editing.mode }
              : undefined
          }
          pending={formPending}
          error={formError}
          onClose={() => {
            setFormOpen(false);
            setEditing(null);
          }}
          onSubmit={(values) => void (formMode === "create" ? handleCreate(values) : handleEdit(values))}
        />
      )}

      {/* Delete confirmation -------------------------------------------------- */}
      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete project"
        description={
          pendingDelete ? (
            <>
              Permanently delete{" "}
              <span className="font-medium text-zinc-200">{pendingDelete.name}</span> and all of
              its data — website, audits, findings, and changes. This cannot be undone.
            </>
          ) : (
            ""
          )
        }
        confirmLabel="Delete project"
        pendingLabel="Deleting…"
        pending={deletePending}
        error={deleteError}
        confirmKeyword={pendingDelete?.name}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void handleDeleteConfirmed()}
      />
    </div>
  );
}
