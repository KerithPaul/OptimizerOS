"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  ApiError,
  connectGitHub,
  disconnectGitHub,
  getGitHub,
  githubOAuthUrl,
  listChangeSets,
  listGitHubRepositories,
  publishChangeSet,
  refreshPullRequestCi,
  selectGitHubRepository,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import type {
  ChangeSetSummary,
  GitHubProject,
  GitHubRepo,
  GithubCiStatus,
  GithubCommit,
  GithubPullRequest,
} from "@/lib/types";

const CI_STYLES: Record<GithubCiStatus, string> = {
  pending: "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30",
  success: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  failure: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
  error: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
  unknown: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
  unavailable: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
};

const PR_STYLES: Record<string, string> = {
  open: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  merged: "bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/30",
  closed: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
  failed: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
};

export default function GitHubPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [data, setData] = useState<GitHubProject | null>(null);
  const [changeSets, setChangeSets] = useState<ChangeSetSummary[]>([]);
  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [fullName, setFullName] = useState("");
  const [pat, setPat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [github, sets] = await Promise.all([getGitHub(projectId), listChangeSets(projectId)]);
    setData(github);
    setChangeSets(sets);
    setFullName((current) => github.connection.selected_repo || current || "");
    if (github.connection.has_token) {
      try {
        const listed = await listGitHubRepositories(projectId);
        setRepos(listed.items);
      } catch (err) {
        setRepos([]);
        if (err instanceof ApiError) {
          setError(err.message);
        }
      }
    } else {
      setRepos([]);
    }
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void load().catch((err) => {
      setError(err instanceof ApiError ? err.message : "Failed to load GitHub state.");
    });
  }, [user, load]);

  useEffect(() => {
    const connected = searchParams.get("github");
    const oauthError = searchParams.get("github_error");
    if (connected === "connected") {
      setNotice("GitHub account connected. Select a repository, then index it from Code intelligence.");
    }
    if (oauthError) {
      setError(oauthError);
    }
    if (connected || oauthError) {
      router.replace(`/projects/${projectId}/github`);
    }
  }, [searchParams, projectId, router]);

  async function onConnect(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setPending("connect");
    try {
      await connectGitHub(projectId, pat);
      setPat("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to store GitHub PAT.");
    } finally {
      setPending(null);
    }
  }

  async function onSelectRepo(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setPending("repo");
    try {
      await selectGitHubRepository(projectId, fullName);
      const payload = await getGitHub(projectId);
      setData(payload);
      setFullName(payload.connection.selected_repo || fullName);
      setNotice(
        `Repository saved: ${payload.connection.selected_repo}. Index it from Code intelligence to clone and profile.`,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to attach GitHub repository.");
    } finally {
      setPending(null);
    }
  }

  async function onDisconnect() {
    setError(null);
    setNotice(null);
    setPending("disconnect");
    try {
      await disconnectGitHub(projectId);
      setFullName("");
      setRepos([]);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to disconnect GitHub.");
    } finally {
      setPending(null);
    }
  }

  async function onRefresh(pr: GithubPullRequest) {
    setError(null);
    setPending(`ci-${pr.id}`);
    try {
      await refreshPullRequestCi(projectId, pr.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to refresh CI.");
    } finally {
      setPending(null);
    }
  }

  async function onPublish(changeSetId: number) {
    setError(null);
    setNotice(null);
    setPending(`publish-${changeSetId}`);
    try {
      const result = await publishChangeSet(projectId, changeSetId);
      if (result.pull_request?.html_url) {
        setNotice(`Pull request opened: ${result.pull_request.html_url}`);
      } else if (result.pull_request_skipped_reason) {
        setNotice(result.pull_request_skipped_reason);
      } else if (result.error) {
        setError(result.error);
      } else {
        setNotice(
          `Pushed ${result.commit.branch}${result.commit.sha ? ` (${result.commit.sha.slice(0, 12)})` : ""}.`,
        );
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "GitHub PR failure.");
    } finally {
      setPending(null);
    }
  }

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  const connection = data?.connection;
  const commits = data?.commits ?? [];
  const pullRequests = data?.pull_requests ?? [];
  const mode = data?.mode;
  const caps = connection?.capabilities;
  let connectionCopy =
    "No GitHub account connected. COMMIT and CREATE_PR need OAuth (repo scope) or a PAT.";
  if (connection?.has_token && connection.auth_type === "oauth2") {
    connectionCopy = `GitHub account connected${connection.login ? ` as ${connection.login}` : ""}. Token is encrypted and never sent to the LLM.`;
  } else if (connection?.has_token) {
    connectionCopy =
      "A GitHub PAT is stored encrypted. It is never shown and never sent to the LLM.";
  }

  return (
    <div className="mx-auto w-full max-w-3xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <ProjectTabs projectId={projectId} />

      <div className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight">GitHub</h1>
        <p className="mt-1 max-w-2xl text-sm text-zinc-400">
          Branch, logical commit, pull request, and CI for reviewed Change Sets.
        </p>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      {/* Connection */}
      <section className="card card-section mb-8 p-5">
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">Connection</h2>
        <p className="mb-3 text-sm leading-relaxed text-zinc-400">
          {connection?.has_token
            ? "A GitHub PAT is stored encrypted. It is never shown and never sent to the LLM."
            : "No GitHub PAT connected. COMMIT and CREATE_PR need a token with repo, pull-request, and checks access."}
        </p>
        {connection?.has_token && (
          <p className="mb-4 text-xs text-zinc-500">
            Selected repository:{" "}
            {connection.selected_repo ? (
              <code className="text-zinc-800 dark:text-zinc-200">{connection.selected_repo}</code>
            ) : (
              "none yet — select a repository and save it"
            )}
          </p>
        )}
        {caps && (
          <ul className="mb-4 grid grid-cols-2 gap-1 text-xs text-zinc-500">
            <li>source: {String(caps.source_access)}</li>
            <li>AST: {String(caps.ast_access ?? false)}</li>
            <li>git history: {String(caps.git_history ?? false)}</li>
            <li>code modification: {caps.code_modification ?? "none"}</li>
            <li>semantic rollback: {caps.automatic_rollback}</li>
            <li>PR: {caps.pull_request ?? "none"}</li>
          </ul>
        )}
        <form onSubmit={onConnect} className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs text-zinc-500">
            Personal access token
            <input
              type="password"
              value={pat}
              onChange={(event) => setPat(event.target.value)}
              autoComplete="off"
              className="field-input"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={!pat || pending === "connect"}
              className="btn-primary"
            >
              Save token
            </button>
            {connection?.has_token && (
              <button
                type="button"
                onClick={() => void onDisconnect()}
                disabled={pending === "disconnect"}
                className="btn-danger"
              >
                Remove token
              </button>
            )}
          </div>
        </form>
      </section>

      {/* Pull requests */}
      <section className="card card-section mb-8 p-5">
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">Pull requests</h2>
        {pullRequests.length === 0 ? (
          <p className="text-sm italic text-zinc-500">No pull requests yet.</p>
        ) : (
          <ul className="space-y-3">
            {pullRequests.map((pr) => (
              <li key={pr.id} className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-sm">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className={`badge ${PR_STYLES[pr.status] ?? PR_STYLES.closed}`}>
                    {pr.status}
                  </span>
                  <span className={`badge ${CI_STYLES[pr.ci_status]}`}>CI {pr.ci_status}</span>
                  <span className="text-xs text-zinc-500">Change Set #{pr.change_set_id}</span>
                </div>
                <p className="font-medium text-zinc-200">{pr.title}</p>
                <p className="mt-1 text-xs text-zinc-500">
                  {pr.head_branch} → {pr.base_branch}
                  {pr.number != null ? ` · #${pr.number}` : ""}
                </p>
                {pr.html_url && (
                  <a
                    href={pr.html_url}
                    className="mt-1 inline-block text-xs text-brand-400 transition-colors hover:text-brand-300"
                    target="_blank"
                    rel="noreferrer"
                  >
                    {pr.html_url}
                  </a>
                )}
                {pr.error && (
                  <p role="alert" className="mt-2 text-xs text-red-400">
                    {pr.error}
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => void onRefresh(pr)}
                  disabled={pending === `ci-${pr.id}`}
                  className="btn-secondary mt-3 px-2.5 py-1 text-xs"
                >
                  Refresh CI
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Commits */}
      <section className="card card-section mb-8 p-5">
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">Commits</h2>
        {commits.length === 0 ? (
          <p className="text-sm italic text-zinc-500">No logical commits yet.</p>
        ) : (
          <ul className="space-y-3">
            {commits.map((commit) => (
              <CommitRow key={commit.id} commit={commit} />
            ))}
          </ul>
        )}
      </section>

      {/* Publish */}
      <section className="card p-5">
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">Publish a Change Set</h2>
        <p className="mb-3 text-sm leading-relaxed text-zinc-400">
          {mode === "COMMIT"
            ? "This project is in COMMIT mode. Publish pushes a branch and does not open a pull request. Switch the project to CREATE_PR on the project page, then use Create pull request."
            : mode === "CREATE_PR"
              ? "This project is in CREATE_PR mode. Publish pushes a branch and opens one pull request. A change set that is already pushed can still be turned into a pull request."
              : "COMMIT pushes a branch. CREATE_PR also opens one pull request. Set the project mode on the project page."}
        </p>
        {changeSets.length === 0 ? (
          <p className="text-sm italic text-zinc-500">No Change Sets to publish.</p>
        ) : (
          <ul className="space-y-2">
            {changeSets.map((set) => {
              const commit = commits.find((row) => row.change_set_id === set.id);
              const pr = pullRequests.find((row) => row.change_set_id === set.id);
              const pushed = commit?.status === "pushed";
              const prOpen = pr != null && pr.status !== "failed";
              const canCreatePr = pushed && !prOpen && mode === "CREATE_PR";
              const blockedByCommitMode = pushed && !prOpen && mode === "COMMIT";
              return (
                <li
                  key={set.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm"
                >
                  <span className="min-w-0 text-zinc-300">
                    <span className="text-zinc-500">#{set.id}</span> ·{" "}
                    <span className="line-clamp-1">{set.objective}</span>
                    {pushed && (
                      <span className="mt-0.5 block text-xs text-zinc-500">
                        Pushed {commit?.branch}
                        {commit?.sha ? ` · ${commit.sha.slice(0, 12)}` : ""}
                        {prOpen ? ` · pull request ${pr?.status}` : ""}
                      </span>
                    )}
                    {blockedByCommitMode && (
                      <span className="mt-0.5 block text-xs text-zinc-500">
                        No pull request, because the project mode is COMMIT.
                      </span>
                    )}
                  </span>
                  {prOpen ? (
                    <span className="text-xs text-zinc-500">Pull request recorded</span>
                  ) : blockedByCommitMode ? null : (
                    <button
                      type="button"
                      onClick={() => void onPublish(set.id)}
                      disabled={pending === `publish-${set.id}`}
                      className="btn-secondary shrink-0 px-2.5 py-1 text-xs"
                    >
                      {canCreatePr ? "Create pull request" : "Publish"}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

function CommitRow({ commit }: { commit: GithubCommit }) {
  return (
    <li className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-sm">
      <p className="font-medium text-zinc-200">{commit.branch}</p>
      <p className="mt-1 font-mono text-xs text-zinc-500">{commit.sha ?? "(no sha)"}</p>
      <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-zinc-400">
        {commit.message}
      </p>
      <p className="mt-1 text-xs text-zinc-500">
        {commit.status} · {commit.files_json.length} file
        {commit.files_json.length === 1 ? "" : "s"}
      </p>
      {commit.error && (
        <p role="alert" className="mt-2 text-xs text-red-400">
          {commit.error}
        </p>
      )}
    </li>
  );
}
