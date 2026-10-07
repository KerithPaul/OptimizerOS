"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  ApiError,
  attachWebsite,
  createJob,
  getLatestAnalysisRun,
  getProject,
  getProjectCapabilities,
  getRepository,
  getWebsite,
  listFindings,
  startWebsiteCrawl,
  updateProjectMode,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Breadcrumbs } from "@/components/flow/ui";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import { JobProgress } from "@/components/flow/JobProgress";
import { useJobWatcher } from "@/components/flow/useJobWatcher";
import { JOB_STATUS_STYLES } from "@/components/flow/badges";
import { AuditFindingCard } from "@/components/flow/AuditFindingCard";
import {
  SCORE_TONE_TEXT,
  ScoreMeter,
  ScoreRing,
  percentage,
  scoreTone,
  toneFor,
} from "@/components/flow/metrics";
import {
  PROJECT_MODES,
  type AnalysisRun,
  type Finding,
  type Project,
  type ProjectCapabilities,
  type ProjectMode,
  type Repository,
  type Score,
  type Website,
} from "@/lib/types";

/* ------------------------------------------------------------------ */
/* Audit progress stages (same matchers as before)                     */
/* ------------------------------------------------------------------ */

const PIPELINE_STAGES = [
  { key: "connect", label: "Connecting to website" },
  { key: "crawl", label: "Crawling website pages" },
  { key: "extract", label: "Extracting & rendering pages" },
  { key: "persist", label: "Storing site data" },
  { key: "audit-collect", label: "Collecting audit facts" },
  { key: "structure", label: "Analyzing structure" },
  { key: "ux", label: "Checking UX" },
  { key: "performance", label: "Checking performance" },
  { key: "accessibility", label: "Checking accessibility" },
  { key: "report", label: "Generating audit report" },
] as const;

const STAGE_MATCHERS: { key: string; patterns: RegExp[] }[] = [
  { key: "connect", patterns: [/connect/i, /resolve/i, /dns/i, /attach/i] },
  { key: "crawl", patterns: [/^Crawling/i, /crawl /i, /Crawling \//] },
  {
    key: "extract",
    patterns: [/extract/i, /render/i, /lighthouse/i],
  },
  { key: "persist", patterns: [/persist/i, /writing/i, /finishing/i, /crawl=/i] },
  { key: "audit-collect", patterns: [/collect/i, /search console/i, /site facts/i] },
  { key: "structure", patterns: [/structure/i, /metadata/i, /parse/i] },
  { key: "ux", patterns: [/\bux\b/i, /content/i] },
  { key: "performance", patterns: [/performance/i, /speed/i] },
  { key: "accessibility", patterns: [/accessib/i, /a11y/i] },
  { key: "report", patterns: [/report/i, /score/i, /finding/i, /analy/i, /evaluat/i, /retriev/i, /assembl/i] },
];

function matchedStageIndex(event: ReturnType<typeof useJobWatcher>["event"], phase: "crawl" | "audit"): number {
  if (!event) return -1;
  const haystack = `${event.stage ?? ""} ${event.message ?? ""}`;
  const ranges: Record<typeof phase, [number, number]> = {
    crawl: [0, 3],
    audit: [4, PIPELINE_STAGES.length - 1],
  };
  const [lo, hi] = ranges[phase];
  let best = -1;
  for (let i = lo; i <= hi; i++) {
    if (STAGE_MATCHERS[i].patterns.some((p) => p.test(haystack))) best = i;
  }
  return best;
}

/* ------------------------------------------------------------------ */
/* Small presentational helpers                                        */
/* ------------------------------------------------------------------ */

const PROVIDERS: { label: string; pattern: RegExp }[] = [
  { label: "GitHub", pattern: /github\.com/i },
  { label: "GitLab", pattern: /gitlab\.com/i },
  { label: "Bitbucket", pattern: /bitbucket\.org/i },
];

function detectProvider(url: string): string | null {
  const hit = PROVIDERS.find((p) => p.pattern.test(url));
  if (hit) return hit.label;
  return /^git@/i.test(url.trim()) ? "Self-hosted git" : null;
}

/** owner/repository-name derived from the real repository URL returned by the API. */
function repoSlug(url: string): string | null {
  try {
    const parsed = new URL(url);
    const parts = parsed.pathname.replace(/\.git$/, "").split("/").filter(Boolean);
    if (parts.length >= 2) return `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
    if (parts.length === 1) return parts[0];
    return null;
  } catch {
    return null;
  }
}

/** Compact stat chip used in the audit overview. */
function Stat({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-wider text-zinc-500">{label}</p>
      <p className={`mt-0.5 truncate text-sm font-medium ${value ? "text-zinc-200" : "italic text-zinc-500"}`}>
        {value ?? "Not available"}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [project, setProject] = useState<Project | null>(null);
  const [repository, setRepository] = useState<Repository | null>(null);
  const [website, setWebsite] = useState<Website | null>(null);
  const [capabilities, setCapabilities] = useState<ProjectCapabilities | null>(null);
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [error, setError] = useState<string | null>(null);
  const { event, running, watch, stop, setEvent } = useJobWatcher();

  const justCreated = searchParams.get("created") === "1";

  /* ---------------- data loading (unchanged logic) ---------------- */

  const loadAudit = useCallback(async () => {
    try {
      // Null just means "no audit yet" — the endpoint returns 200 + null.
      setRun((await getLatestAnalysisRun(projectId)) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load audit.");
    }
  }, [projectId]);

  const loadFindings = useCallback(async () => {
    try {
      setFindings(await listFindings(projectId));
    } catch {
      // Non-blocking: the findings page shows the full set; errors surface there.
    }
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    void (async () => {
      try {
        const p = await getProject(projectId);
        if (cancelled) return;
        setProject(p);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load project.");
      }
      // Null responses mean "not attached yet" — the endpoints return 200 + null.
      const repo = await getRepository(projectId);
      if (cancelled) return;
      setRepository(repo);
      const ws = await getWebsite(projectId);
      if (cancelled) return;
      setWebsite(ws);
      try {
        const caps = await getProjectCapabilities(projectId);
        if (cancelled) return;
        setCapabilities(caps);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load capabilities.");
      }
      await loadAudit();
      await loadFindings();
    })();
    return () => {
      cancelled = true;
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, projectId, loadAudit, loadFindings]);

  /* ---------------- website + audit flow (unchanged logic) ---------------- */

  const [urlInput, setUrlInput] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);
  const [attaching, setAttaching] = useState(false);
  const [phase, setPhase] = useState<"idle" | "crawl" | "audit">("idle");

  function validateUrl(value: string): string | null {
    if (!value.trim()) return "Enter your website URL.";
    let parsed: URL;
    try {
      parsed = new URL(value.trim());
    } catch {
      return "Enter a full URL, e.g. https://example.com";
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "The URL must start with http:// or https://";
    }
    return null;
  }

  async function handleAttachAndAudit(e: FormEvent) {
    e.preventDefault();
    const validation = validateUrl(urlInput);
    setUrlError(validation);
    if (validation) return;

    setError(null);
    setAttaching(true);
    try {
      const ws = await attachWebsite(projectId, urlInput.trim());
      setWebsite(ws);
      setUrlInput("");
    } catch (err) {
      setUrlError(
        err instanceof ApiError
          ? err.message
          : "We could not attach that website. Check the URL and try again."
      );
      setAttaching(false);
      return;
    }
    setAttaching(false);

    // The real pipeline is two backend jobs: website_crawl persists the site's
    // pages, then the audit evaluates rules against that stored crawl. Chain
    // them so "Generate audit" delivers a complete audit, not a gap-filled one.
    setEvent(null);
    setPhase("crawl");
    try {
      const crawlJob = await startWebsiteCrawl(projectId);
      watch(crawlJob.id, () => {
        void getWebsite(projectId)
          .then(setWebsite)
          .catch(() => undefined);
        setEvent(null);
        setPhase("audit");
        void (async () => {
          try {
            const auditJob = await createJob(projectId, "audit");
            watch(auditJob.id, () => {
              setPhase("idle");
              void loadAudit();
              void loadFindings();
              void getWebsite(projectId)
                .then(setWebsite)
                .catch(() => undefined);
            });
          } catch (err) {
            setPhase("idle");
            setError(err instanceof ApiError ? err.message : "Failed to start the audit.");
            stop();
          }
        })();
      });
    } catch (err) {
      setPhase("idle");
      setError(err instanceof ApiError ? err.message : "Failed to start the crawl.");
      stop();
    }
  }

  async function rerunAudit() {
    // Re-run the full pipeline so the audit reflects the current site, not a
    // stale crawl. The watcher's onSucceeded chains crawl → audit → refresh.
    setEvent(null);
    setPhase("crawl");
    try {
      const crawlJob = await startWebsiteCrawl(projectId);
      watch(crawlJob.id, () => {
        void getWebsite(projectId)
          .then(setWebsite)
          .catch(() => undefined);
        setEvent(null);
        setPhase("audit");
        void (async () => {
          try {
            const auditJob = await createJob(projectId, "audit");
            watch(auditJob.id, () => {
              setPhase("idle");
              void loadAudit();
              void loadFindings();
            });
          } catch (err) {
            setPhase("idle");
            setError(err instanceof ApiError ? err.message : "Failed to start the audit.");
            stop();
          }
        })();
      });
    } catch (err) {
      setPhase("idle");
      setError(err instanceof ApiError ? err.message : "Failed to start the crawl.");
      stop();
    }
  }

  /* ---------------- dev tools (existing functionality) ---------------- */

  const [devError, setDevError] = useState<string | null>(null);

  async function runDevJob(type: string) {
    setDevError(null);
    setEvent(null);
    try {
      const job = await createJob(projectId, type);
      watch(job.id, () => {
        void loadAudit();
        void loadFindings();
      });
    } catch (err) {
      setDevError(err instanceof ApiError ? err.message : "Failed to start job.");
      stop();
    }
  }

  /* ---------------- derived ---------------- */

  const scores: Score[] = useMemo(
    () => (run?.scores_json ? Object.values(run.scores_json) : []),
    [run]
  );
  const overall = useMemo(() => {
    if (scores.length === 0) return null;
    const total = scores.reduce((sum, s) => sum + s.value, 0);
    const max = scores.reduce((sum, s) => sum + s.max_value, 0);
    return percentage(total, max);
  }, [scores]);

  const topFindings = useMemo(
    () => [...findings].sort((a, b) => b.priority - a.priority).slice(0, 5),
    [findings]
  );

  const stageIndex = event && phase !== "idle" ? matchedStageIndex(event, phase) : -1;
  const phaseLabel =
    phase === "crawl" ? "Crawling your website…" : phase === "audit" ? "Generating your audit…" : null;
  const hasAudit = run !== null && (run.status === "succeeded" || run.status === "partial");

  /* Gap bookkeeping: a "repository" gap is stale when the repo is connected and
   * cloned NOW — the audit simply ran before the connection. Show a re-run hint
   * instead of a scary "missing" warning that contradicts the connected card. */
  const gapNames = run?.gaps_json?.map((g) => g.capability) ?? [];
  const staleRepositoryGap = gapNames.includes("repository") && repository?.clone_status === "cloned";
  const remainingGaps = gapNames.filter((c) => c !== "repository");

  /* Workflow stepper state derived from real loaded data (never faked). */
  const steps = useMemo(
    () => [
      { label: "Project", done: project !== null, current: false },
      { label: "Website", done: website !== null, current: website === null && project !== null },
      { label: "Audit", done: hasAudit, current: website !== null && !hasAudit },
      {
        label: "Repository",
        done: repository !== null,
        current: repository === null && hasAudit,
      },
      {
        label: "Improve",
        done: false,
        current: repository !== null && hasAudit,
      },
    ],
    [project, website, hasAudit, repository]
  );

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <Breadcrumbs
        items={[{ label: "Projects", href: "/projects" }, { label: project?.name ?? "…" }]}
      />

      {/* ---------------- header ---------------- */}
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-gradient">
            {project?.name ?? "Loading…"}
          </h1>
          {project && (
            <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-zinc-500">
              <span className="badge bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30">
                {project.mode.toLowerCase().replace(/_/g, " ")}
              </span>
              <span>Created {new Date(project.created_at).toLocaleDateString()}</span>
            </p>
          )}
        </div>
        <label className="flex items-center gap-2 text-xs text-zinc-500">
          Mode
          <select
            value={project?.mode ?? "AUDIT_ONLY"}
            onChange={async (e) => {
              const mode = e.target.value as ProjectMode;
              setError(null);
              try {
                setProject(await updateProjectMode(projectId, mode));
              } catch (err) {
                setError(err instanceof ApiError ? err.message : "Failed to update mode.");
              }
            }}
            className="field-input w-auto px-2 py-1.5 text-xs"
          >
            {PROJECT_MODES.map((mode) => {
              const allowed = (capabilities?.allowed_modes ?? PROJECT_MODES).includes(mode);
              return (
                <option key={mode} value={mode} disabled={!allowed}>
                  {mode}
                </option>
              );
            })}
          </select>
        </label>
      </div>

      <ProjectTabs projectId={projectId} />

      {/* ---------------- workflow stepper (derived from real data) ---------------- */}
      <ol className="mb-8 flex flex-wrap items-center gap-x-2 gap-y-2 text-xs">
        {steps.map((step, index) => (
          <li key={step.label} className="flex items-center gap-2">
            <span
              aria-current={step.current ? "step" : undefined}
              className={`flex size-5 items-center justify-center rounded-full text-[10px] font-bold ${
                step.done
                  ? "bg-emerald-500/20 text-emerald-300 ring-1 ring-inset ring-emerald-500/40"
                  : step.current
                    ? "bg-brand-500 text-white"
                    : "border border-zinc-700 text-zinc-500"
              }`}
            >
              {step.done ? "✓" : index + 1}
            </span>
            <span
              className={
                step.current ? "font-medium text-brand-300" : step.done ? "text-zinc-300" : "text-zinc-500"
              }
            >
              {step.label}
            </span>
            {index < steps.length - 1 && (
              <span aria-hidden="true" className="text-zinc-700">
                →
              </span>
            )}
          </li>
        ))}
      </ol>

      {capabilities && !capabilities.allowed_modes.includes("APPLY_LOCALLY") && (
        <p className="note-info mb-6">
          APPLY_LOCALLY, COMMIT, and CREATE_PR need a git repository (or a modifying platform
          connector). A URL-only website can be crawled and recommended, but cannot be patched.
        </p>
      )}
      {capabilities?.allowed_modes.includes("APPLY_LOCALLY") &&
        website?.platform === "url_only" &&
        repository && (
          <p className="note-info mb-6">
            The live site is URL-only (no production writes). APPLY_LOCALLY patches the attached
            git workspace.
          </p>
        )}

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      {justCreated && (
        <div className="note-success mb-8 flex items-start gap-3">
          <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-xs font-bold text-white">
            ✓
          </span>
          <div>
            <p className="text-sm font-semibold text-emerald-200">
              Project created successfully
            </p>
            <p className="mt-0.5 text-sm text-emerald-300/90">
              Next: enter your website URL below so ArchitectOS can analyze it and generate your
              first audit.
            </p>
          </div>
        </div>
      )}

      {/* ---------------- step: website + audit ---------------- */}
      <section className="mb-10">
        <div className="card overflow-hidden">
          <div className="card-section p-5 sm:p-6">
            <h2 className="text-base font-semibold text-zinc-100">Analyze your website</h2>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-zinc-400">
              {website
                ? "ArchitectOS is tracking this website. Run an audit any time — crawling is read-only; nothing on your site is modified."
                : "Enter your live website URL and let ArchitectOS analyze the experience. It will crawl the site read-only and evaluate structure, UX, performance, and accessibility."}
            </p>

            {!website ? (
              <form onSubmit={handleAttachAndAudit} className="mt-5" noValidate>
                <label className="field-label" htmlFor="website-url">
                  Website URL
                </label>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <input
                    id="website-url"
                    type="url"
                    inputMode="url"
                    value={urlInput}
                    onChange={(e) => {
                      setUrlInput(e.target.value);
                      if (urlError) setUrlError(null);
                    }}
                    placeholder="https://yourwebsite.com"
                    aria-invalid={urlError ? true : undefined}
                    aria-describedby={urlError ? "website-url-error" : undefined}
                    className="field-input flex-1"
                  />
                  <button type="submit" disabled={attaching || running} className="btn-primary">
                    {attaching ? "Connecting…" : running ? "Working…" : "Generate audit →"}
                  </button>
                </div>
                {urlError ? (
                  <p id="website-url-error" role="alert" className="field-error">
                    {urlError}
                  </p>
                ) : (
                  <p className="field-hint">
                    We will connect, crawl, and analyze your site, then show the audit right
                    below.
                  </p>
                )}
              </form>
            ) : (
              <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-800 bg-zinc-900/70 p-4">
                <div className="min-w-0">
                  <p className="eyebrow mb-0.5">Attached website</p>
                  <p className="truncate text-sm font-medium text-zinc-100">{website.url}</p>
                  <p className="text-xs text-zinc-500">
                    Platform: {website.platform}
                    {website.latest_crawl?.status
                      ? ` · last crawl: ${website.latest_crawl.status}`
                      : ""}
                  </p>
                </div>
                <button onClick={rerunAudit} disabled={running} className="btn-secondary">
                  {running
                    ? phaseLabel ?? "Working…"
                    : "Re-crawl & re-run audit"}
                </button>
              </div>
            )}
          </div>

          {/* ---------------- audit progress ---------------- */}
          {running && (
            <JobProgress
              title={phaseLabel ?? "Analyzing your website…"}
              event={event}
              stages={PIPELINE_STAGES}
              matchedStageIndex={stageIndex}
            />
          )}

          {/* ---------------- audit results ---------------- */}
          {hasAudit && run && (
            <div className="card-section bg-gradient-to-b from-brand-950/20 to-transparent p-5 sm:p-6">
              <div className="mb-5 flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-base font-semibold text-zinc-100">Audit overview</h3>
                <div className="flex items-center gap-2">
                  <span
                    className={`badge ${
                      run.status === "partial"
                        ? "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30"
                        : "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30"
                    }`}
                  >
                    {run.status === "partial" ? "Completed (partial data)" : "Completed"}
                  </span>
                  {run.finished_at && (
                    <span className="text-xs text-zinc-500">
                      Last analyzed {new Date(run.finished_at).toLocaleString()}
                    </span>
                  )}
                </div>
              </div>

              <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-start">
                {overall != null && (
                  <div className="card flex flex-col items-center gap-1 p-5">
                    <ScoreRing percentage={overall} tone={scoreTone(overall)} />
                    <p className="text-xs text-zinc-500">Overall score</p>
                  </div>
                )}
                <div className="grid flex-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {scores.map((score) => {
                    const p = percentage(score.value, score.max_value);
                    const tone = toneFor(score.value, score.max_value);
                    return (
                      <div key={score.key} className="card card-hover p-4">
                        <p className="text-xs font-medium text-zinc-400">{score.label}</p>
                        <p className={`mt-1 text-2xl font-bold tabular-nums ${SCORE_TONE_TEXT[tone]}`}>
                          {score.value}
                          <span className="text-sm font-normal text-zinc-500">
                            /{score.max_value}
                          </span>
                        </p>
                        <div className="mt-2">
                          <ScoreMeter percentage={p} tone={tone} />
                        </div>
                        <p className="mt-2 text-xs text-zinc-500">
                          {score.signals.length} signal{score.signals.length === 1 ? "" : "s"}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </div>

              {run.gaps_json && run.gaps_json.length > 0 && staleRepositoryGap && (
                <p className="note-info mt-4">
                  This audit ran before your repository was connected
                  {run.finished_at
                    ? ` — last analyzed ${new Date(run.finished_at).toLocaleString()}`
                    : ""}
                  . Re-run the audit to include repository insights.
                  {remainingGaps.length > 0 && ` Still missing: ${remainingGaps.join(", ")}.`}{" "}
                  <button onClick={rerunAudit} disabled={running} className="font-semibold underline">
                    Re-run audit
                  </button>
                </p>
              )}
              {run.gaps_json && run.gaps_json.length > 0 && !staleRepositoryGap && (
                <p className="note-warning mt-4">
                  Partial run — missing: {gapNames.join(", ")}
                </p>
              )}

              <div className="mt-6 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="Website" value={website?.url ?? null} />
                <Stat
                  label="Findings"
                  value={findings.length > 0 ? String(findings.length) : hasAudit ? "0" : null}
                />
                <Stat
                  label="Status"
                  value={
                    run.status === "partial"
                      ? "Completed (partial data)"
                      : run.status === "succeeded"
                        ? "Completed"
                        : run.status
                  }
                />
                <Stat
                  label="Last analyzed"
                  value={run.finished_at ? new Date(run.finished_at).toLocaleString() : null}
                />
              </div>

              {/* top findings */}
              <div className="mt-6">
                <div className="mb-3 flex items-center justify-between">
                  <h4 className="text-sm font-semibold text-zinc-100">Top opportunities</h4>
                  <Link
                    href={`/projects/${projectId}/findings`}
                    className="text-xs font-medium text-brand-400 transition-colors hover:text-brand-300"
                  >
                    View all {findings.length} finding{findings.length === 1 ? "" : "s"} →
                  </Link>
                </div>
                {topFindings.length === 0 ? (
                  <p className="text-sm italic text-zinc-500">
                    No findings recorded for this run.
                  </p>
                ) : (
                  <ul className="space-y-2.5">
                    {topFindings.map((finding) => (
                      <li key={finding.id} className="card card-hover p-4">
                        <AuditFindingCard
                          ruleId={finding.rule}
                          observation={finding.observation}
                          affectedResource={finding.affected_resource}
                          severity={finding.severity}
                          category={finding.category}
                          evidence={finding.evidence}
                          variant="compact"
                        />
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="mt-6 flex flex-wrap gap-2">
                {repository ? (
                  <Link href={`/projects/${projectId}/agents`} className="btn-primary">
                    Improve SEO · AEO · GEO →
                  </Link>
                ) : (
                  <Link href={`/projects/${projectId}/code`} className="btn-primary">
                    Connect repo →
                  </Link>
                )}
                <Link href={`/projects/${projectId}/findings`} className="btn-secondary">
                  View all {findings.length} finding{findings.length === 1 ? "" : "s"}
                </Link>
              </div>
            </div>
          )}

          {!hasAudit && !running && run?.status === "failed" && (
            <div className="card-section p-5 sm:p-6">
              <p role="alert" className="note-error">
                The last audit failed{run.error ? `: ${run.error}` : "."}{" "}
                <button onClick={rerunAudit} className="font-semibold underline">
                  Try again
                </button>
              </p>
            </div>
          )}

          {!hasAudit && !running && !run && (
            <div className="card-section p-5 sm:p-6">
              <div className="flex flex-col items-center gap-3 py-6 text-center">
                <div className="flex size-12 items-center justify-center rounded-full bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30">
                  <svg viewBox="0 0 24 24" fill="none" className="size-6" aria-hidden="true">
                    <path
                      d="M12 3v3m0 12v3m9-9h-3M6 12H3m13.5-6.5-2 2m-7 7-2 2m11 0-2-2m-7-7-2-2"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                    />
                  </svg>
                </div>
                <p className="text-sm font-semibold text-zinc-100">No audit available yet</p>
                <p className="max-w-sm text-sm leading-relaxed text-zinc-400">
                  {website
                    ? "Generate your first audit to see scores, findings, and recommendations."
                    : "Attach a website above and generate your first audit."}
                </p>
                {website && (
                  <button onClick={rerunAudit} className="btn-primary mt-1">
                    Generate audit
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      </section>

      {/* ---------------- step: repository (on the Code page) ---------------- */}
      <section className="mb-10">
        {repository ? (
          <div className="card p-5 sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h2 className="text-base font-semibold text-zinc-100">Repository</h2>
                  <span className="badge bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
                    Connected
                  </span>
                </div>
                <p className="mt-1 truncate text-sm font-medium text-zinc-200">
                  {repoSlug(repository.url) ?? repository.url}
                </p>
                <p className="truncate text-xs text-zinc-500">
                  {repository.url} · {detectProvider(repository.url) ?? "git"} ·{" "}
                  {repository.default_branch}
                  {repository.framework ? ` · ${repository.framework}` : ""}
                </p>
              </div>
              <Link href={`/projects/${projectId}/agents`} className="btn-primary shrink-0">
                Improve SEO · AEO · GEO →
              </Link>
            </div>
          </div>
        ) : (
          <div className="card p-5 sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-base font-semibold text-zinc-100">Improve your project</h2>
                <p className="mt-1 max-w-xl text-sm leading-relaxed text-zinc-400">
                  Connect your repository to let ArchitectOS understand the underlying codebase
                  and provide deeper project insights. Optional — the audit works without it.
                </p>
              </div>
              <Link href={`/projects/${projectId}/code`} className="btn-primary shrink-0">
                Connect repo →
              </Link>
            </div>
          </div>
        )}
      </section>

      {/* ---------------- details + links ---------------- */}
      <section className="mb-10">
        <div className="card overflow-hidden">
          <dl className="divide-y divide-zinc-800/70">
            {[
              {
                label: "Platform",
                value:
                  repository && website
                    ? `git + ${website.platform}`
                    : website?.platform ?? (repository ? "Git repository" : null),
              },
              { label: "Repository", value: repository?.url ?? null },
              { label: "Website", value: website?.url ?? null },
              {
                label: "Modification",
                value: capabilities?.report
                  ? capabilities.report.seo_modification === "none"
                    ? "no"
                    : capabilities.report.seo_modification
                  : null,
              },
              { label: "Framework", value: repository?.framework ?? null },
              { label: "Indexing state", value: repository?.indexing_state ?? null },
            ].map((field) => (
              <div
                key={field.label}
                className="flex items-center justify-between gap-4 px-5 py-3 text-sm"
              >
                <dt className="font-medium text-zinc-300">{field.label}</dt>
                <dd
                  className={`truncate text-right ${
                    field.value ? "text-zinc-200" : "italic text-zinc-500"
                  }`}
                >
                  {field.value ?? "Not available"}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {/* ---------------- dev tools (existing functionality) ---------------- */}
      <details className="card mb-4 p-5">
        <summary className="cursor-pointer text-sm font-medium text-zinc-500 transition-colors hover:text-zinc-300">
          Developer tools
        </summary>
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            onClick={() => runDevJob("health_ping")}
            disabled={running}
            className="btn-secondary"
          >
            Run health check
          </button>
          <button
            onClick={() => runDevJob("health_ping_fail")}
            disabled={running}
            className="btn-secondary"
          >
            Run health check (force failure)
          </button>
          <button onClick={() => runDevJob("audit")} disabled={running} className="btn-secondary">
            Run audit
          </button>
        </div>
        {devError && (
          <p role="alert" className="note-error mt-3">
            {devError}
          </p>
        )}
        {event && (
          <div className="mt-4 text-sm">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-zinc-300">{event.stage ?? event.message}</span>
              <span className={`badge ${JOB_STATUS_STYLES[event.job_status]}`}>
                {event.job_status}
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full rounded-full bg-zinc-100 transition-all"
                style={{ width: `${event.percent ?? 0}%` }}
              />
            </div>
            {event.message && <p className="mt-1 text-xs text-zinc-500">{event.message}</p>}
          </div>
        )}
      </details>
    </div>
  );
}
