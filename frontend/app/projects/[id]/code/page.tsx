"use client";

import { useParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  attachRepository,
  getRepository,
  indexRepository,
  listRepositoryFiles,
  listRepositoryNeighbors,
  listRepositoryRoutes,
  listRepositorySymbols,
  updateRepositoryToken,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import { JobProgress } from "@/components/flow/JobProgress";
import { useJobWatcher } from "@/components/flow/useJobWatcher";
import type {
  CodeFile,
  CodeNeighbor,
  CodeRoute,
  CodeSymbol,
  Repository,
} from "@/lib/types";

export default function CodeIntelligencePage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [repository, setRepository] = useState<Repository | null>(null);
  const [missingRepo, setMissingRepo] = useState(false);
  const [url, setUrl] = useState("");
  const [cloneToken, setCloneToken] = useState("");
  const [tokenInput, setTokenInput] = useState("");
  const [savingToken, setSavingToken] = useState(false);
  const [files, setFiles] = useState<CodeFile[]>([]);
  const [symbols, setSymbols] = useState<CodeSymbol[]>([]);
  const [routes, setRoutes] = useState<CodeRoute[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [neighbors, setNeighbors] = useState<CodeNeighbor[]>([]);
  const [error, setError] = useState<string | null>(null);
  const { event, running, watch, stop, setEvent } = useJobWatcher();

  const loadIntelligence = useCallback(async () => {
    const [fileRows, symbolRows, routeRows] = await Promise.all([
      listRepositoryFiles(projectId),
      listRepositorySymbols(projectId),
      listRepositoryRoutes(projectId),
    ]);
    setFiles(fileRows);
    setSymbols(symbolRows);
    setRoutes(routeRows);
  }, [projectId]);

  const loadRepository = useCallback(async () => {
    try {
      // Null just means "no repository attached yet" — the endpoint returns 200 + null.
      const repo = (await getRepository(projectId)) ?? null;
      setRepository(repo);
      setMissingRepo(repo === null);
      if (repo !== null && (repo.indexing_state === "indexed" || repo.clone_status === "cloned")) {
        await loadIntelligence();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load repository.");
    }
  }, [projectId, loadIntelligence]);

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    loadRepository();
    return () => stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loadRepository is stable per projectId
  }, [user]);

  const startIndex = useCallback(async () => {
    setError(null);
    setEvent(null);
    try {
      const job = await indexRepository(projectId);
      watch(job.id, loadRepository);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start indexing.");
      stop();
    }
  }, [projectId, loadRepository, watch, stop, setEvent]);

  async function onAttach(event_: FormEvent) {
    event_.preventDefault();
    setError(null);
    try {
      const repo = await attachRepository(projectId, url, "main", cloneToken);
      setRepository(repo);
      setMissingRepo(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to attach repository.");
    }
  }

  async function onSaveToken(event_: FormEvent) {
    event_.preventDefault();
    setError(null);
    setSavingToken(true);
    try {
      const repo = await updateRepositoryToken(projectId, tokenInput);
      setRepository(repo);
      setTokenInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save access token.");
    } finally {
      setSavingToken(false);
    }
  }

  async function onSelectSymbol(name: string) {
    setSelected(name);
    try {
      const [calls, deps] = await Promise.all([
        listRepositoryNeighbors(projectId, name, "calls"),
        listRepositoryNeighbors(projectId, name, "depends_on"),
      ]);
      setNeighbors([
        ...calls.map((row) => ({ ...row, relation: row.relation ?? "CALLS" })),
        ...deps.map((row) => ({ ...row, relation: row.relation ?? "DEPENDS_ON" })),
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load neighbours.");
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
    <div className="mx-auto w-full max-w-5xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <ProjectTabs projectId={projectId} />

      <div className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight">Code intelligence</h1>
        <p className="mt-1 max-w-2xl text-sm text-zinc-400">
          Index your repository so ArchitectOS understands files, symbols, routes, and
          dependencies — the foundation for code-aware improvements.
        </p>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      {missingRepo && (
        <form onSubmit={onAttach} className="card mb-8 p-5">
          <h2 className="text-sm font-semibold text-zinc-100">Connect your repository</h2>
          <p className="mt-1 text-sm leading-relaxed text-zinc-400">
            Attach a git URL to this project, then index it to build the code graph.
          </p>
          <label className="field-label mt-4" htmlFor="repo-attach-url">
            Repository URL
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              id="repo-attach-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://github.com/org/repo.git"
              className="field-input flex-1"
            />
            <button type="submit" className="btn-primary">
              Attach
            </button>
          </div>
          <label className="field-label mt-3" htmlFor="repo-attach-token">
            Access token <span className="font-normal text-zinc-500">(optional)</span>
          </label>
          <input
            id="repo-attach-token"
            value={cloneToken}
            onChange={(e) => setCloneToken(e.target.value)}
            placeholder="Only needed for private repositories"
            type="password"
            autoComplete="off"
            className="field-input"
          />
        </form>
      )}

      {repository && (
        <>
          {/* Repository summary */}
          <div className="card mb-6 p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="eyebrow mb-0.5">Repository</p>
                <p className="truncate text-sm font-medium text-zinc-100">{repository.url}</p>
              </div>
              <button onClick={startIndex} disabled={running} className="btn-primary shrink-0">
                {running ? "Indexing…" : "Index repository"}
              </button>
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              {[
                { label: "Framework", value: repository.framework },
                {
                  label: "Indexing state",
                  value: repository.indexing_state ?? "pending",
                },
                {
                  label: "Clone",
                  value: repository.clone_status,
                },
              ].map((field) => (
                <div
                  key={field.label}
                  className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 px-3 py-2.5"
                >
                  <p className="text-[11px] uppercase tracking-wider text-zinc-500">
                    {field.label}
                  </p>
                  <p
                    className={`mt-0.5 truncate text-sm font-medium ${
                      field.value ? "text-zinc-200" : "italic text-zinc-500"
                    }`}
                  >
                    {field.value ?? "Not available"}
                  </p>
                </div>
              ))}
            </div>
          </div>

          <JobProgress title="Indexing job" event={event} />

          {/* Improve hand-off — the next step in the product flow. */}
          <div className="card mt-6 flex flex-wrap items-center justify-between gap-3 bg-gradient-to-r from-brand-950/30 to-transparent p-5">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-zinc-100">
                Ready to improve SEO, AEO & GEO
              </h2>
              <p className="mt-1 max-w-xl text-sm leading-relaxed text-zinc-400">
                The repository is indexed and the code graph is available. Send the optimization
                agents to work on real findings from your audit — high-confidence, low-risk
                changes only.
              </p>
            </div>
            <Link href={`/projects/${projectId}/agents`} className="btn-primary shrink-0">
              Improve SEO · AEO · GEO →
            </Link>
          </div>

          {/* Access token */}
          <form onSubmit={onSaveToken} className="card card-section mt-6 p-5">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-zinc-100">Access token</h2>
              <span
                className={`badge ${
                  repository.has_clone_token
                    ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30"
                    : "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30"
                }`}
              >
                {repository.has_clone_token ? "Token set" : "No token (public clone only)"}
              </span>
            </div>
            <p className="mb-4 text-sm leading-relaxed text-zinc-400">
              Needed for private repositories. Cloning without one fails with an authentication
              error rather than hanging.
            </p>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                placeholder={repository.has_clone_token ? "Replace access token" : "Access token"}
                type="password"
                autoComplete="off"
                aria-label="Access token"
                className="field-input flex-1"
              />
              <button type="submit" disabled={savingToken} className="btn-primary">
                {savingToken ? "Saving…" : "Save token"}
              </button>
            </div>
          </form>

          {/* Index explorer */}
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            <section className="card overflow-hidden">
              <div className="card-section px-4 py-3">
                <h2 className="text-sm font-semibold text-zinc-100">Files ({files.length})</h2>
              </div>
              <ul className="max-h-80 space-y-0.5 overflow-auto p-3 text-sm">
                {files.map((file) => (
                  <li
                    key={file.path}
                    className="truncate rounded px-1.5 py-1 font-mono text-xs text-zinc-400"
                    title={file.path}
                  >
                    {file.path}
                  </li>
                ))}
                {files.length === 0 && (
                  <li className="px-1.5 py-4 italic text-zinc-500">No files indexed yet</li>
                )}
              </ul>
            </section>
            <SymbolsSection
              symbols={symbols}
              selected={selected}
              onSelect={onSelectSymbol}
            />
            <section className="card overflow-hidden">
              <div className="card-section px-4 py-3">
                <h2 className="text-sm font-semibold text-zinc-100">Routes ({routes.length})</h2>
              </div>
              <ul className="max-h-80 space-y-0.5 overflow-auto p-3 text-sm">
                {routes.map((route) => (
                  <li
                    key={route.path}
                    className="truncate rounded px-1.5 py-1 font-mono text-xs text-zinc-400"
                    title={route.path}
                  >
                    {route.path}
                  </li>
                ))}
                {routes.length === 0 && (
                  <li className="px-1.5 py-4 italic text-zinc-500">No routes indexed yet</li>
                )}
              </ul>
            </section>
          </div>

          {selected && (
            <section className="card mt-4 p-5">
              <h2 className="mb-3 text-sm font-semibold text-zinc-100">
                Neighbours of {selected}
              </h2>
              <ul className="space-y-1.5 text-sm">
                {neighbors.map((row, index) => (
                  <li
                    key={`${row.relation}:${row.name}:${index}`}
                    className="flex flex-wrap items-center gap-2 text-zinc-400"
                  >
                    <span className="badge bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30">
                      {row.relation ?? "related"}
                    </span>
                    {row.name}
                    <span className="text-xs text-zinc-500">{row.kind}</span>
                  </li>
                ))}
                {neighbors.length === 0 && (
                  <li className="italic text-zinc-500">No neighbours</li>
                )}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function SymbolsSection({
  symbols,
  selected,
  onSelect,
}: {
  symbols: CodeSymbol[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  return (
    <section className="card overflow-hidden">
      <div className="card-section px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-100">Symbols ({symbols.length})</h2>
      </div>
      <ul className="max-h-80 space-y-0.5 overflow-auto p-3 text-sm">
        {symbols.map((symbol) => (
          <li key={`${symbol.kind}:${symbol.file_path}:${symbol.name}:${symbol.start_line}`}>
            <button
              type="button"
              onClick={() => onSelect(symbol.name)}
              className={`w-full truncate rounded px-1.5 py-1 text-left transition-colors hover:bg-zinc-800/60 ${
                selected === symbol.name
                  ? "bg-brand-500/10 font-medium text-brand-300"
                  : "text-zinc-400"
              }`}
            >
              {symbol.name} <span className="text-xs text-zinc-500">{symbol.kind}</span>
            </button>
          </li>
        ))}
        {symbols.length === 0 && (
          <li className="px-1.5 py-4 italic text-zinc-500">No symbols indexed yet</li>
        )}
      </ul>
    </section>
  );
}
