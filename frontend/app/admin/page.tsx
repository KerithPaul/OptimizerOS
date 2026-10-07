"use client";

/**
 * Admin Dashboard Overview: statistics cards + recent projects.
 * Stats derive from the projects list (real API); a users count appears
 * only when the users endpoint responds. Everything degrades to the app's
 * standard loading / empty / error states.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  adminListProjects,
  adminListUsers,
  type AdminStats,
} from "@/lib/admin";
import { ApiError } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { PageHeader } from "@/components/flow/ui";
import { ErrorState, ListSkeleton } from "@/components/flow/states";
import {
  DataTable,
  IconAction,
  ModeBadge,
  TableCell,
  TableRow,
  StatCard,
  formatDate,
} from "@/components/admin/adminui";
import type { Project } from "@/lib/types";

const RECENT_LIMIT = 6;

function ProjectsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
      <path
        d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function UsersIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
      <path
        d="M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1m18 0v-1a4 4 0 0 0-2.5-3.7M15 4.6a3.5 3.5 0 0 1 0 6.8M12 7.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function AdminDashboardPage() {
  const { user, loading: userLoading } = useCurrentUser();
  const [projects, setProjects] = useState<Project[]>([]);
  const [ownerNames, setOwnerNames] = useState<Map<number, string>>(new Map());
  const [stats, setStats] = useState<AdminStats>({ totalProjects: 0, totalUsers: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([adminListProjects(), adminListUsers().catch(() => null)])
      .then(([projectRows, userRows]) => {
        setProjects(projectRows);
        setStats({
          totalProjects: projectRows.length,
          totalUsers: userRows ? userRows.length : null,
        });
        // owner id → display label (name if set, else email)
        const owners = new Map<number, string>();
        for (const u of userRows ?? []) owners.set(u.id, u.name || u.email);
        setOwnerNames(owners);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Failed to load dashboard data."),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!user) return;
    load();
  }, [user, load]);

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  const recent = [...projects]
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, RECENT_LIMIT);

  const ownerLabel = (createdBy: number) => ownerNames.get(createdBy) ?? `user #${createdBy}`;

  return (
    <div className="space-y-8">
      <PageHeader
        title="Dashboard"
        description="System-wide overview of projects and users across ArchitectOS."
      />

      {/* Statistics ---------------------------------------------------------- */}
      <section aria-label="Statistics">
        <div className="grid gap-4 sm:grid-cols-2">
          <StatCard
            label="Total Projects"
            value={loading ? null : stats.totalProjects}
            hint={loading ? undefined : "All projects in the system"}
            loading={loading}
            icon={<ProjectsIcon />}
          />
          <StatCard
            label="Total Users"
            value={loading ? null : stats.totalUsers}
            hint={
              loading
                ? undefined
                : stats.totalUsers === null
                  ? "User API not connected yet"
                  : "Registered accounts"
            }
            loading={loading}
            icon={<UsersIcon />}
          />
        </div>
      </section>

      {/* Recent projects ----------------------------------------------------- */}
      <section aria-label="Recent projects">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <h2 className="eyebrow">Recent projects</h2>
          <Link href="/admin/projects" className="btn-secondary px-3 py-1.5 text-xs">
            View all projects
          </Link>
        </div>

        {loading ? (
          <ListSkeleton rows={4} />
        ) : error ? (
          <ErrorState title="Couldn't load projects" message={error} onRetry={load} />
        ) : recent.length === 0 ? (
          <div className="card p-10 text-center">
            <p className="text-sm font-semibold text-zinc-100">No projects yet</p>
            <p className="mx-auto mt-1 max-w-sm text-sm leading-relaxed text-zinc-400">
              Projects created in the app will appear here, most recent first.
            </p>
            <Link href="/admin/projects" className="btn-primary mt-4">
              Go to projects
            </Link>
          </div>
        ) : (
          <DataTable columns={["Project", "Owner", "Created", "Status", ""]}>
            {recent.map((project) => (
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
                <TableCell className="text-right">
                  <IconAction
                    label={`Open project ${project.name}`}
                    onClick={() => {
                      window.location.href = `/projects/${project.id}`;
                    }}
                  >
                    <svg viewBox="0 0 24 24" fill="none" className="size-4" aria-hidden="true">
                      <path
                        d="M5 12h14m-6-6 6 6-6 6"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </IconAction>
                </TableCell>
              </TableRow>
            ))}
          </DataTable>
        )}
      </section>
    </div>
  );
}
