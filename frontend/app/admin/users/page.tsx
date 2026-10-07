"use client";

/**
 * Admin Users management: table with full CRUD interaction flow. The
 * backend user-management endpoints do not exist yet, so calls resolve
 * against /admin/users (see lib/admin.ts) and surface standard error
 * states — swap in real data by implementing the API alone.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  adminCreateUser,
  adminDeleteUser,
  adminListUsers,
  adminUpdateUser,
  type AdminUser,
} from "@/lib/admin";
import { ApiError } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { PageHeader } from "@/components/flow/ui";
import { ErrorState, ListSkeleton } from "@/components/flow/states";
import {
  Avatar,
  ConfirmDialog,
  DataTable,
  IconAction,
  RoleBadge,
  SearchInput,
  StatusBadge,
  TableCell,
  TableRow,
  formatDate,
} from "@/components/admin/adminui";
import { UserFormModal, type UserFormValues } from "@/components/admin/UserFormModal";

export default function AdminUsersPage() {
  const { user, loading: userLoading } = useCurrentUser();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  // create / edit modal state
  const [formOpen, setFormOpen] = useState(false);
  const [formMode, setFormMode] = useState<"create" | "edit">("create");
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [formPending, setFormPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // delete dialog state
  const [pendingDelete, setPendingDelete] = useState<AdminUser | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    adminListUsers()
      .then(setUsers)
      .catch((err) =>
        setError(
          err instanceof ApiError && err.status === 403
            ? "Only admins can manage users."
            : err instanceof ApiError
              ? err.message
              : "Failed to load users.",
        ),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!user) return;
    load();
  }, [user, load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        u.name.toLowerCase().includes(q) ||
        u.email.toLowerCase().includes(q) ||
        u.role.includes(q),
    );
  }, [users, query]);

  async function handleCreate(values: UserFormValues) {
    setFormPending(true);
    setFormError(null);
    try {
      await adminCreateUser({
        name: values.name,
        email: values.email,
        password: values.password ?? "",
        role: values.role,
        project_limit: values.projectLimit,
      });
      setFormOpen(false);
      load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to create the user.");
    } finally {
      setFormPending(false);
    }
  }

  async function handleEdit(values: UserFormValues) {
    if (!editing) return;
    setFormPending(true);
    setFormError(null);
    try {
      await adminUpdateUser(editing.id, {
        name: values.name,
        email: values.email,
        role: values.role,
        status: values.status,
        project_limit: values.projectLimit,
        project_limit_set: true,
        ...(values.password ? { password: values.password } : {}),
      });
      setFormOpen(false);
      setEditing(null);
      load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to update the user.");
    } finally {
      setFormPending(false);
    }
  }

  async function handleDeleteConfirmed() {
    if (!pendingDelete) return;
    setDeletePending(true);
    setDeleteError(null);
    try {
      await adminDeleteUser(pendingDelete.id);
      setPendingDelete(null);
      load();
    } catch (err) {
      setDeleteError(
        err instanceof ApiError ? err.message : "Failed to delete the user. Try again.",
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

  return (
    <div className="space-y-6">
      <PageHeader
        title="Users"
        description="Everyone with access to this ArchitectOS instance, their roles, and account status."
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
            + Add user
          </button>
        }
      />

      {loading ? (
        <ListSkeleton rows={5} />
      ) : error ? (
        <ErrorState title="Couldn't load users" message={error} onRetry={load} />
      ) : users.length === 0 ? (
        <div className="card p-10 text-center">
          <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-full bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30">
            <svg viewBox="0 0 24 24" fill="none" className="size-6" aria-hidden="true">
              <path
                d="M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1m18 0v-1a4 4 0 0 0-2.5-3.7M15 4.6a3.5 3.5 0 0 1 0 6.8M12 7.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <p className="text-sm font-semibold text-zinc-100">No users yet</p>
          <p className="mx-auto mt-1 max-w-sm text-sm leading-relaxed text-zinc-400">
            User accounts will appear here once the users API is connected.
          </p>
        </div>
      ) : (
        <>
          <SearchInput
            label="users"
            value={query}
            onChange={setQuery}
            placeholder="Search by name, email, or role…"
          />

          {filtered.length === 0 ? (
            <div className="card p-10 text-center">
              <p className="text-sm font-semibold text-zinc-100">No matches</p>
              <p className="mx-auto mt-1 max-w-sm text-sm leading-relaxed text-zinc-400">
                No users match “{query.trim()}”.
              </p>
            </div>
          ) : (
            <DataTable
              columns={[
                "User",
                "Email",
                "Role",
                "Status",
                "Project limit",
                "Created",
                "Actions",
              ]}
              footer={
                <p className="text-xs text-zinc-500">
                  {filtered.length} of {users.length} user{users.length === 1 ? "" : "s"}
                </p>
              }
            >
              {filtered.map((u) => (
                <TableRow key={u.id}>
                  <TableCell>
                    <div className="flex items-center gap-3">
                      <Avatar name={u.name} email={u.email} />
                      <span className="font-medium text-zinc-100">{u.name}</span>
                    </div>
                  </TableCell>
                  <TableCell className="text-zinc-400">{u.email}</TableCell>
                  <TableCell>
                    <RoleBadge role={u.role} />
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={u.status} />
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-zinc-400">
                    {u.role === "admin"
                      ? "Unlimited"
                      : u.project_limit == null
                        ? "Unlimited"
                        : `${u.project_limit} project${u.project_limit === 1 ? "" : "s"}`}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-zinc-400">
                    {formatDate(u.created_at)}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-0.5">
                      <IconAction
                        label={`Edit user ${u.name}`}
                        onClick={() => {
                          setEditing(u);
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
                        label={`Delete user ${u.name}`}
                        tone="danger"
                        disabled={u.email === user.email}
                        onClick={() => {
                          setPendingDelete(u);
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
        <UserFormModal
          open={formOpen}
          mode={formMode}
          initial={
            formMode === "edit" && editing
              ? {
                  name: editing.name,
                  email: editing.email,
                  role: editing.role,
                  status: editing.status,
                  projectLimit: editing.project_limit,
                }
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

      {/* Delete confirmation --------------------------------------------------- */}
      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete user"
        description={
          pendingDelete ? (
            <>
              Permanently delete <span className="font-medium text-zinc-200">{pendingDelete.name}</span>{" "}
              ({pendingDelete.email}) and remove their access. This cannot be undone.
            </>
          ) : (
            ""
          )
        }
        confirmLabel="Delete user"
        pendingLabel="Deleting…"
        pending={deletePending}
        error={deleteError}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void handleDeleteConfirmed()}
      />
    </div>
  );
}
