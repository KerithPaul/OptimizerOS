"use client";

/**
 * Admin data layer. Projects use the long-standing endpoints; user
 * management uses the new /admin/users API (admin-gated). Stats derive
 * from the data the dashboard already fetches.
 */

import { request } from "./api";
import type { Project, ProjectMode, User } from "./types";

export type UserRole = "admin" | "member";
export type UserStatus = "active" | "suspended";

export interface AdminUser {
  id: number;
  name: string;
  email: string;
  role: UserRole;
  status: UserStatus;
  /** Max projects this account may create; null = unlimited (admins). */
  project_limit: number | null;
  created_at: string;
}

export interface AdminUserCreateInput {
  name: string;
  email: string;
  password: string;
  role: UserRole;
  /** Max projects a member may create; null/undefined = unlimited. */
  project_limit?: number | null;
}

export interface AdminUserUpdateInput {
  name?: string;
  email?: string;
  password?: string;
  role?: UserRole;
  status?: UserStatus;
  project_limit?: number | null;
  /** True when project_limit is intentionally present (incl. clearing it). */
  project_limit_set?: boolean;
}

export interface AdminStats {
  totalProjects: number;
  totalUsers: number | null;
}

/* -------------------------------------------------------------------------- */
/* Projects (real API)                                                        */
/* -------------------------------------------------------------------------- */

export function adminListProjects(): Promise<Project[]> {
  return request<Project[]>("/projects");
}

export function adminCreateProject(name: string, mode: ProjectMode): Promise<Project> {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify({ name, mode }),
  });
}

export function adminUpdateProjectMode(projectId: number, mode: ProjectMode): Promise<Project> {
  return request<Project>(`/projects/${projectId}/mode`, {
    method: "PATCH",
    body: JSON.stringify({ mode }),
  });
}

export function adminDeleteProject(projectId: number): Promise<void> {
  return request<void>(`/projects/${projectId}`, { method: "DELETE" });
}

/* -------------------------------------------------------------------------- */
/* Users (/admin/users — implemented in app/api/v1/admin_users.py)             */
/* -------------------------------------------------------------------------- */

export function adminListUsers(): Promise<AdminUser[]> {
  return request<AdminUser[]>("/admin/users");
}

export function adminCreateUser(input: AdminUserCreateInput): Promise<AdminUser> {
  return request<AdminUser>("/admin/users", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function adminUpdateUser(
  userId: number,
  patch: AdminUserUpdateInput,
): Promise<AdminUser> {
  return request<AdminUser>(`/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function adminDeleteUser(userId: number): Promise<void> {
  return request<void>(`/admin/users/${userId}`, { method: "DELETE" });
}

export type { Project, ProjectMode, User };
