"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  ApiError,
  connectWordPress,
  disconnectWordPress,
  getWordPress,
  listWordPressRevisions,
  listWordPressSnapshots,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import type {
  WordPressPage,
  WordPressProject,
  WordPressRevision,
  WordPressSnapshot,
} from "@/lib/types";

export default function WordPressPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [data, setData] = useState<WordPressProject | null>(null);
  const [snapshots, setSnapshots] = useState<WordPressSnapshot[]>([]);
  const [revisions, setRevisions] = useState<WordPressRevision[]>([]);
  const [revisionUrl, setRevisionUrl] = useState("");
  const [url, setUrl] = useState("");
  const [username, setUsername] = useState("");
  const [applicationPassword, setApplicationPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [wordpress, stored] = await Promise.all([
      getWordPress(projectId),
      listWordPressSnapshots(projectId),
    ]);
    setData(wordpress);
    setSnapshots(stored);
    if (wordpress.connection.url) {
      setUrl((current) => current || wordpress.connection.url || "");
    }
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void load().catch((err) => {
      setError(err instanceof ApiError ? err.message : "Failed to load WordPress state.");
    });
  }, [user, load]);

  async function onConnect(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setPending("connect");
    try {
      await connectWordPress(projectId, url, username, applicationPassword);
      setApplicationPassword("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "WordPress authentication failure.");
    } finally {
      setPending(null);
    }
  }

  async function onDisconnect() {
    setError(null);
    setPending("disconnect");
    try {
      await disconnectWordPress(projectId);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove WordPress credentials.");
    } finally {
      setPending(null);
    }
  }

  async function onLoadRevisions(page: WordPressPage) {
    setError(null);
    setPending(`rev-${page.url}`);
    try {
      const rows = await listWordPressRevisions(projectId, page.url);
      setRevisionUrl(page.url);
      setRevisions(rows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "WordPress API failure.");
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
  const pages = data?.pages ?? [];
  const caps = connection?.capabilities;

  return (
    <div className="mx-auto w-full max-w-3xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <ProjectTabs projectId={projectId} />

      <div className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight">WordPress</h1>
        <p className="mt-1 max-w-2xl text-sm text-zinc-400">
          Connected site, capabilities, pages, metadata, changes, and revisions. No git
          repository is required.
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
          {connection?.has_credentials
            ? "An Application Password is stored encrypted. It is never shown and never sent to the LLM."
            : "No Application Password connected. WordPress REST writes need a username and application password."}
        </p>
        {connection?.seo_plugin && (
          <p className="mb-3 text-xs text-zinc-500">
            Detected SEO plugin: {connection.seo_plugin}
          </p>
        )}
        {caps && (
          <ul className="mb-4 grid grid-cols-2 gap-1 text-xs text-zinc-500">
            <li>source: {String(caps.source_access)}</li>
            <li>theme source: {String(caps.theme_source)}</li>
            <li>content: {String(caps.content_access)}</li>
            <li>metadata: {String(caps.metadata_access)}</li>
            <li>SEO modification: {caps.seo_modification}</li>
            <li>AEO modification: {caps.aeo_modification}</li>
            <li>GEO modification: {caps.geo_modification}</li>
            <li>theme modification: {caps.theme_modification}</li>
            <li>automatic rollback: {caps.automatic_rollback}</li>
            <li>snapshot: {String(caps.snapshot)}</li>
          </ul>
        )}
        <form onSubmit={onConnect} className="space-y-3">
          <label className="flex flex-col gap-1 text-xs text-zinc-500">
            Site URL
            <input
              type="url"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              className="field-input mt-1"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-zinc-500">
            Username
            <input
              type="text"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="off"
              className="field-input mt-1"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-zinc-500">
            Application password
            <input
              type="password"
              value={applicationPassword}
              onChange={(event) => setApplicationPassword(event.target.value)}
              autoComplete="off"
              className="field-input mt-1"
            />
          </label>
          <div className="flex flex-wrap gap-2 pt-1">
            <button
              type="submit"
              disabled={!url || !username || !applicationPassword || pending === "connect"}
              className="btn-primary"
            >
              Save credentials
            </button>
            {connection?.has_credentials && (
              <button
                type="button"
                onClick={() => void onDisconnect()}
                disabled={pending === "disconnect"}
                className="btn-danger"
              >
                Remove credentials
              </button>
            )}
          </div>
        </form>
      </section>

      {/* Pages */}
      <section className="card card-section mb-8 p-5">
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">Pages</h2>
        {pages.length === 0 ? (
          <p className="text-sm italic text-zinc-500">No pages ingested yet.</p>
        ) : (
          <ul className="space-y-3">
            {pages.map((page) => (
              <li
                key={page.url}
                className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-sm"
              >
                <p className="font-medium text-zinc-200">{page.title || page.url}</p>
                <p className="mt-1 break-all text-xs text-zinc-500">{page.url}</p>
                <p className="mt-1 text-xs text-zinc-500">
                  description: {page.meta_description || "(none)"} · canonical:{" "}
                  {page.canonical || "(none)"}
                </p>
                <button
                  type="button"
                  onClick={() => void onLoadRevisions(page)}
                  disabled={pending === `rev-${page.url}`}
                  className="btn-secondary mt-3 px-2.5 py-1 text-xs"
                >
                  Revisions
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Revisions */}
      <section className="card card-section mb-8 p-5">
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">Revisions</h2>
        {revisionUrl ? (
          <p className="mb-2 break-all text-xs text-zinc-500">{revisionUrl}</p>
        ) : (
          <p className="text-sm italic text-zinc-500">
            Select a page to load WordPress revisions.
          </p>
        )}
        {revisions.length > 0 && (
          <ul className="space-y-2">
            {revisions.map((row) => (
              <li key={row.id} className="text-sm text-zinc-400">
                #{row.id} {row.title || ""} {row.date || ""}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Snapshots */}
      <section className="card p-5">
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">ArchitectOS snapshots</h2>
        {snapshots.length === 0 ? (
          <p className="text-sm italic text-zinc-500">No WordPress snapshots yet.</p>
        ) : (
          <ul className="space-y-2">
            {snapshots.map((row) => (
              <li key={row.id} className="text-sm text-zinc-400">
                #{row.id} · {row.reason} · {row.created_at}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
