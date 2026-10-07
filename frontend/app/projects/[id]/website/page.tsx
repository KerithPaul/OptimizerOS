"use client";

import { useParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  ApiError,
  attachWebsite,
  getWebsite,
  getWebsitePage,
  listWebsitePages,
  startWebsiteCrawl,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import { BackToOverview } from "@/components/flow/ui";
import { JobProgress } from "@/components/flow/JobProgress";
import { useJobWatcher } from "@/components/flow/useJobWatcher";
import type {
  Website,
  WebsitePageDetail,
  WebsitePageSummary,
} from "@/lib/types";

export default function WebsiteIntelligencePage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [website, setWebsite] = useState<Website | null>(null);
  const [missingWebsite, setMissingWebsite] = useState(false);
  const [url, setUrl] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);
  const [pages, setPages] = useState<WebsitePageSummary[]>([]);
  const [selected, setSelected] = useState<WebsitePageDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { event, running, watch, stop, setEvent } = useJobWatcher();

  const loadWebsite = useCallback(async () => {
    try {
      // Null just means "no website attached yet" — the endpoint returns 200 + null.
      const attached = (await getWebsite(projectId)) ?? null;
      setWebsite(attached);
      setMissingWebsite(attached === null);
      if (attached === null) {
        setPages([]);
        return;
      }
      const rows = await listWebsitePages(projectId);
      setPages(rows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load website.");
    }
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    void (async () => {
      try {
        const attached = (await getWebsite(projectId)) ?? null;
        if (cancelled) return;
        setWebsite(attached);
        setMissingWebsite(attached === null);
        if (attached === null) {
          setPages([]);
          return;
        }
        const rows = await listWebsitePages(projectId);
        if (cancelled) return;
        setPages(rows);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load website.");
      }
    })();
    return () => {
      cancelled = true;
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, projectId]);

  const startCrawl = useCallback(async () => {
    setError(null);
    setEvent(null);
    try {
      const job = await startWebsiteCrawl(projectId);
      watch(job.id, loadWebsite);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start crawl.");
      stop();
    }
  }, [projectId, loadWebsite, watch, stop, setEvent]);

  async function onAttach(event_: FormEvent) {
    event_.preventDefault();
    setError(null);
    setUrlError(null);
    const value = url.trim();
    let invalid: string | null = null;
    try {
      const parsed = new URL(value);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        invalid = "The URL must start with http:// or https://";
      }
    } catch {
      invalid = "Enter a full URL, e.g. https://example.com";
    }
    if (invalid) {
      setUrlError(invalid);
      return;
    }
    try {
      const attached = await attachWebsite(projectId, value);
      setWebsite(attached);
      setMissingWebsite(false);
      setUrl("");
    } catch (err) {
      setUrlError(err instanceof ApiError ? err.message : "Failed to attach website.");
    }
  }

  async function onSelectPage(pageId: number) {
    setError(null);
    try {
      const detail = await getWebsitePage(projectId, pageId);
      setSelected(detail);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load page.");
    }
  }

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  const model = selected?.model_json ?? null;
  const structured = model && Array.isArray(model.structured_data) ? model.structured_data : [];
  const headings = model && Array.isArray(model.headings) ? model.headings : [];
  const modification =
    website?.capabilities.seo_modification === "none" ? "no" : website?.capabilities.seo_modification;

  return (
    <div className="mx-auto w-full max-w-5xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <BackToOverview projectId={projectId} />
      <ProjectTabs projectId={projectId} />

      <div className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight">Website intelligence</h1>
        <p className="mt-1 max-w-2xl text-sm text-zinc-400">
          Crawl your live site read-only and inspect what ArchitectOS sees: pages, metadata,
          structured data, and speed signals.
        </p>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      {missingWebsite && (
        <form onSubmit={onAttach} className="card mb-8 p-5" noValidate>
          <h2 className="text-sm font-semibold text-zinc-100">Attach your website</h2>
          <p className="mt-1 text-sm leading-relaxed text-zinc-400">
            Attach a public HTTP(S) URL to crawl. URL-only cannot modify the live site. If a git
            repository is attached, you can still APPLY_LOCALLY to patch the workspace.
          </p>
          <label className="field-label mt-4" htmlFor="website-attach-url">
            Website URL
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              id="website-attach-url"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                if (urlError) setUrlError(null);
              }}
              placeholder="https://example.com/"
              aria-invalid={urlError ? true : undefined}
              className="field-input flex-1"
            />
            <button type="submit" className="btn-primary">
              Attach
            </button>
          </div>
          {urlError && (
            <p role="alert" className="field-error">
              {urlError}
            </p>
          )}
        </form>
      )}

      {website && (
        <>
          {/* Website summary */}
          <div className="card mb-6 p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="eyebrow mb-0.5">Website</p>
                <p className="truncate text-sm font-medium text-zinc-100">{website.url}</p>
              </div>
              <button onClick={startCrawl} disabled={running} className="btn-primary shrink-0">
                {running ? "Crawling…" : "Start crawl"}
              </button>
            </div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {[
                { label: "Platform", value: website.platform ?? null },
                { label: "Modification", value: modification ?? null },
                { label: "Snapshot / rollback", value: website.capabilities.snapshot ? "yes" : "no" },
                {
                  label: "Latest crawl",
                  value: website.latest_crawl
                    ? website.latest_crawl.status
                    : null,
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
            {website.latest_crawl && (
              <p className="mt-3 text-xs text-zinc-500">
                {typeof website.latest_crawl.page_count === "number"
                  ? `${website.latest_crawl.page_count} pages`
                  : pages.length
                    ? `${pages.length} pages`
                    : ""}
                {sitemapUrlCount(website.latest_crawl.stats_json) != null
                  ? ` · sitemap ${sitemapUrlCount(website.latest_crawl.stats_json)} URLs`
                  : ""}
                {website.latest_crawl.cap_reason ? ` (${website.latest_crawl.cap_reason})` : ""}
              </p>
            )}
          </div>

          <JobProgress title="Crawl job" event={event} />

          {/* Pages table */}
          <div className="card mb-6 mt-6 overflow-hidden">
            <div className="card-section flex items-center justify-between px-5 py-3">
              <h2 className="text-sm font-semibold text-zinc-100">Pages ({pages.length})</h2>
            </div>
            {pages.length === 0 ? (
              <p className="px-5 py-10 text-center text-sm italic text-zinc-500">
                No pages crawled yet. Start a crawl to populate this list.
              </p>
            ) : (
              <div className="max-h-[28rem] overflow-auto">
                <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
                  <thead className="sticky top-0 bg-zinc-900 text-xs uppercase tracking-wide text-zinc-500">
                    <tr className="border-b border-zinc-800">
                      <th scope="col" className="px-4 py-2.5 font-medium">URL</th>
                      <th scope="col" className="px-4 py-2.5 font-medium">Status</th>
                      <th scope="col" className="px-4 py-2.5 font-medium">Title</th>
                      <th scope="col" className="px-4 py-2.5 font-medium">Words</th>
                      <th scope="col" className="px-4 py-2.5 font-medium">Speed</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/70">
                    {pages.map((page) => {
                      const path = pageUrlPath(page.url);
                      return (
                        <tr
                          key={page.id}
                          tabIndex={0}
                          aria-selected={selected?.id === page.id}
                          className={`cursor-pointer outline-none transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 ${
                            selected?.id === page.id
                              ? "bg-brand-500/10"
                              : "hover:bg-zinc-800/40"
                          }`}
                          onClick={() => onSelectPage(page.id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onSelectPage(page.id);
                            }
                          }}
                        >
                          <td className="max-w-[18rem] truncate px-4 py-2.5 font-medium text-zinc-300">
                            {path}
                          </td>
                          <td className="px-4 py-2.5">
                            <StatusCodeBadge status={page.status_code} />
                          </td>
                          <td className="max-w-[16rem] truncate px-4 py-2.5 text-zinc-400">
                            {page.title || "—"}
                          </td>
                          <td className="px-4 py-2.5 tabular-nums text-zinc-500">
                            {typeof page.word_count === "number" ? page.word_count : "—"}
                          </td>
                          <td className="px-4 py-2.5 tabular-nums text-zinc-500">
                            {formatFetchMs(page.fetch_ms)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Page detail */}
          <div className="card overflow-hidden">
            <div className="card-section px-5 py-3">
              <h2 className="text-sm font-semibold text-zinc-100">
                Raw metadata and structured data
              </h2>
            </div>
            {!selected ? (
              <p className="px-5 py-10 text-center text-sm italic text-zinc-500">
                Select a page above to inspect its metadata.
              </p>
            ) : (
              <div className="max-h-96 space-y-4 overflow-auto p-5 text-sm">
                <MetaRow label="URL" value={selected.url} />
                <MetaRow
                  label="Status"
                  value={selected.status_code != null ? String(selected.status_code) : null}
                />
                <MetaRow label="Speed" value={formatFetchMs(selected.fetch_ms)} />
                <MetaRow label="Title" value={selected.title} />
                <MetaRow
                  label="Description"
                  value={
                    typeof model?.meta_description === "string" ? model.meta_description : null
                  }
                />
                <MetaRow label="Canonical" value={selected.canonical} />
                <MetaRow
                  label="Robots"
                  value={
                    model?.robots && typeof model.robots === "object"
                      ? JSON.stringify(model.robots)
                      : null
                  }
                />
                <div>
                  <p className="eyebrow mb-1">Headings</p>
                  {headings.length === 0 ? (
                    <p className="italic text-zinc-500">None</p>
                  ) : (
                    <ul className="space-y-0.5 text-xs text-zinc-400">
                      {headings.map((heading, index) => {
                        const row = heading as { level?: number; text?: string };
                        return (
                          <li key={`${row.level}:${row.text}:${index}`}>
                            h{row.level} {row.text}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
                <div>
                  <p className="eyebrow mb-1">Structured data</p>
                  <pre className="overflow-auto rounded-lg border border-zinc-800 bg-zinc-950/70 p-3 text-xs text-zinc-400">
                    {JSON.stringify(structured, null, 2)}
                  </pre>
                </div>
                {selected.lighthouse && (
                  <div>
                    <p className="eyebrow mb-1">
                      Lighthouse ({String(selected.lighthouse.provenance_label ?? "lab signal, not ranking")})
                    </p>
                    <pre className="overflow-auto rounded-lg border border-zinc-800 bg-zinc-950/70 p-3 text-xs text-zinc-400">
                      {JSON.stringify(selected.lighthouse, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function MetaRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <p className="eyebrow">{label}</p>
      <p className={value ? "break-all text-zinc-300" : "italic text-zinc-500"}>
        {value ?? "Not available"}
      </p>
    </div>
  );
}

function pageUrlPath(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.pathname}${parsed.search}` || "/";
  } catch {
    return url;
  }
}

function formatFetchMs(value: number | null | undefined): string {
  if (typeof value !== "number" || value < 0) return "—";
  return `${value}ms`;
}

function StatusCodeBadge({ status }: { status: number | null }) {
  if (status == null) {
    return <span className="text-zinc-500">—</span>;
  }
  const tone =
    status >= 200 && status < 300
      ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30"
      : status >= 300 && status < 400
        ? "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30"
        : "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30";
  return <span className={`badge ${tone}`}>{status}</span>;
}

function sitemapUrlCount(stats: Record<string, unknown> | null | undefined): number | null {
  if (!stats) return null;
  const nested = stats.crawl;
  const crawl = nested && typeof nested === "object" ? (nested as Record<string, unknown>) : stats;
  const sitemap = crawl.sitemap;
  if (!sitemap || typeof sitemap !== "object") return null;
  const count = (sitemap as Record<string, unknown>).page_url_count;
  return typeof count === "number" ? count : null;
}
