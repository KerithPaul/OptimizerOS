"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  ApiError,
  connectSearchConsole,
  disconnectSearchConsole,
  getSearchConsole,
  searchConsoleOAuthUrl,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import type { SearchConsoleProject, SearchConsoleRow } from "@/lib/types";

function formatCtr(ctr: number): string {
  return `${(ctr * 100).toFixed(1)}%`;
}

export default function SearchConsolePage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [data, setData] = useState<SearchConsoleProject | null>(null);
  const [propertyUrl, setPropertyUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const load = useCallback(async () => {
    const payload = await getSearchConsole(projectId);
    setData(payload);
    setPropertyUrl((current) => payload.connection.property_url || current || "");
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void load().catch((err) => {
      setError(err instanceof ApiError ? err.message : "Failed to load Search Console.");
    });
  }, [user, load]);

  useEffect(() => {
    const connected = searchParams.get("gsc");
    const oauthError = searchParams.get("gsc_error");
    if (connected === "connected") {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- sync state from OAuth redirect
      setNotice(
        "Google account connected. Select a property if more than one is listed, then run an audit."
      );
    }
    if (oauthError) {
      setError(oauthError);
    }
    if (connected || oauthError) {
      router.replace(`/projects/${projectId}/search-console`);
    }
  }, [searchParams, projectId, router]);

  async function onSaveProperty(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setPending("property");
    try {
      await connectSearchConsole(projectId, propertyUrl);
      const payload = await getSearchConsole(projectId);
      setData(payload);
      setPropertyUrl(payload.connection.property_url || propertyUrl);
      if (payload.connection.property_url !== propertyUrl) {
        setError(
          "Save returned success but the selected property was not stored. Try Save property again."
        );
        return;
      }
      setNotice(
        `Property saved: ${payload.connection.property_url}. Page and query metrics are loaded when you run an audit, not when you save the property.`
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save Search Console property.");
    } finally {
      setPending(null);
    }
  }

  async function onDisconnect() {
    setError(null);
    setNotice(null);
    setPending("disconnect");
    try {
      await disconnectSearchConsole(projectId);
      setPropertyUrl("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to disconnect Search Console.");
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
  const rows = data?.rows ?? [];
  const pageRows = rows.filter((row) => row.dimension === "page");
  const queryRows = rows.filter((row) => row.dimension === "query");
  const properties = connection?.properties ?? [];

  return (
    <div className="mx-auto w-full max-w-3xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <ProjectTabs projectId={projectId} />

      <div className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight">Search Console</h1>
        <p className="mt-1 max-w-2xl text-sm text-zinc-400">
          Connect your Google account with OAuth 2.0. ArchitectOS stores a refresh token
          encrypted and never shows it. Real GSC data outranks LLM opinion.
        </p>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}
      {notice && (
        <p className="note-success mb-6">
          {notice}
        </p>
      )}

      {/* Connection */}
      <section className="card card-section mb-8 p-5">
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">Connection</h2>
        <p className="mb-3 text-sm leading-relaxed text-zinc-300">
          {connection?.display ?? "Search Console: unavailable"}
        </p>
        <p className="mb-4 text-xs text-zinc-500">
          Google account: {connection?.has_stored_credentials ? "connected" : "not connected"} ·
          OAuth client: {connection?.oauth_configured ? "configured" : "missing in env"}
        </p>
        {connection?.has_stored_credentials && (
          <p className="mb-4 text-xs text-zinc-500">
            Saved property:{" "}
            {connection.property_url ? (
              <code className="kbd">{connection.property_url}</code>
            ) : (
              "none yet — select a property and click Save property"
            )}
          </p>
        )}
        {connection?.oauth_redirect_uri && (
          <p className="mb-4 break-all text-xs text-zinc-500">
            Authorized redirect URI to add in Google Cloud (Web client, exact match):{" "}
            <code className="kbd">{connection.oauth_redirect_uri}</code>
          </p>
        )}
        {!connection?.oauth_configured && (
          <p className="note-warning mb-4 text-xs">
            Set GSC_OAUTH_CLIENT_ID and GSC_OAUTH_CLIENT_SECRET in backend/.env (not the frontend
            env), then restart the API and worker.
          </p>
        )}
        <div className="mb-4 flex flex-wrap gap-2">
          <a
            href={searchConsoleOAuthUrl(projectId)}
            className={`btn-primary ${connection?.oauth_configured ? "" : "pointer-events-none opacity-50"}`}
          >
            Connect with Google
          </a>
          {connection?.has_stored_credentials && (
            <button
              type="button"
              onClick={() => void onDisconnect()}
              disabled={pending === "disconnect"}
              className="btn-danger"
            >
              Disconnect
            </button>
          )}
        </div>
        {connection?.has_stored_credentials && (
          <form onSubmit={onSaveProperty} className="space-y-3 border-t border-zinc-800/70 pt-4">
            <label className="flex flex-col gap-1 text-xs text-zinc-500">
              GSC property
              {properties.length > 0 ? (
                <select
                  value={propertyUrl}
                  onChange={(event) => setPropertyUrl(event.target.value)}
                  className="field-input mt-1"
                >
                  <option value="">Select a property</option>
                  {properties.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  value={propertyUrl}
                  onChange={(event) => setPropertyUrl(event.target.value)}
                  placeholder="https://example.com/ or sc-domain:example.com"
                  className="field-input mt-1"
                />
              )}
            </label>
            <button
              type="submit"
              disabled={!propertyUrl || pending === "property"}
              className="btn-secondary"
            >
              {pending === "property" ? "Saving…" : "Save property"}
            </button>
          </form>
        )}
      </section>

      <MetricsTable title="Pages" rows={pageRows} kind="page" projectId={projectId} />
      <MetricsTable title="Queries" rows={queryRows} kind="query" projectId={projectId} />
    </div>
  );
}

function MetricsTable({
  title,
  rows,
  kind,
  projectId,
}: {
  title: string;
  rows: SearchConsoleRow[];
  kind: "page" | "query";
  projectId: number;
}) {
  return (
    <section className="card mb-8 overflow-hidden">
      <h2 className="card-section px-5 py-3 text-sm font-semibold text-zinc-100">{title}</h2>
      {rows.length === 0 ? (
        <p className="px-5 py-6 text-sm italic text-zinc-500">
          No {kind} metrics yet. Saving a property only stores which GSC property to use.{" "}
          <Link
            href={`/projects/${projectId}`}
            className="not-italic text-brand-400 transition-colors hover:text-brand-300"
          >
            Run an audit
          </Link>{" "}
          to pull page and query metrics from Google.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-zinc-500">
              <tr className="border-b border-zinc-800">
                <th scope="col" className="px-5 py-2.5 font-medium">
                  {kind}
                </th>
                <th scope="col" className="px-5 py-2.5 font-medium">
                  impressions
                </th>
                <th scope="col" className="px-5 py-2.5 font-medium">
                  clicks
                </th>
                <th scope="col" className="px-5 py-2.5 font-medium">
                  CTR
                </th>
                <th scope="col" className="px-5 py-2.5 font-medium">
                  position
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {rows.map((row) => (
                <tr key={row.id} className="transition-colors hover:bg-zinc-800/40">
                  <td className="max-w-xs truncate px-5 py-2.5 text-zinc-300">
                    {kind === "page" ? row.page : row.query}
                  </td>
                  <td className="px-5 py-2.5 tabular-nums text-zinc-400">{row.impressions}</td>
                  <td className="px-5 py-2.5 tabular-nums text-zinc-400">{row.clicks}</td>
                  <td className="px-5 py-2.5 tabular-nums text-zinc-400">{formatCtr(row.ctr)}</td>
                  <td className="px-5 py-2.5 tabular-nums text-zinc-400">
                    {row.position.toFixed(1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
