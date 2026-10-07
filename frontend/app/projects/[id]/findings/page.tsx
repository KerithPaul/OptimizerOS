"use client";

import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, listFindings } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { BackToOverview } from "@/components/flow/ui";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import {
  CONFIDENCE_OPTIONS,
  FINDING_STATUS_OPTIONS,
  SEVERITY_OPTIONS,
  SEVERITY_STYLES,
} from "@/components/flow/badges";
import { AuditFindingCard } from "@/components/flow/AuditFindingCard";
import { getFindingCopy, getSeverityLabel } from "@/lib/findingMappings";
import type {
  Finding,
  FindingFilters,
  FindingStatus,
  RuleCategory,
  RuleConfidence,
  RuleSeverity,
} from "@/lib/types";

const CATEGORY_OPTIONS: { value: RuleCategory | ""; label: string }[] = [
  { value: "", label: "All categories" },
  { value: "technical_seo", label: "Technical SEO" },
  { value: "content_seo", label: "Content SEO" },
  { value: "aeo", label: "AEO" },
  { value: "geo", label: "GEO" },
  { value: "agent_accessibility", label: "Agent accessibility" },
];

export default function FindingsPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [category, setCategory] = useState<RuleCategory | "">("");
  const [severity, setSeverity] = useState<RuleSeverity | "">("");
  const [confidence, setConfidence] = useState<RuleConfidence | "">("");
  const [status, setStatus] = useState<FindingStatus | "">("");
  const [url, setUrl] = useState(searchParams.get("url") ?? "");
  const [rule, setRule] = useState(searchParams.get("rule") ?? "");
  const [findings, setFindings] = useState<Finding[]>([]);
  const [selected, setSelected] = useState<Finding | null>(null);
  const [expandedRule, setExpandedRule] = useState<string | null>(searchParams.get("rule"));
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const filters: FindingFilters = {};
        if (category) filters.category = category;
        if (severity) filters.severity = severity;
        if (confidence) filters.confidence = confidence;
        if (status) filters.status = status;
        if (url.trim()) filters.url = url.trim();
        if (rule.trim()) filters.rule = rule.trim();
        const rows = await listFindings(projectId, filters);
        if (cancelled) return;
         
        setFindings(rows);
        setSelected((current) => rows.find((row) => row.id === current?.id) ?? null);
      } catch (err) {
        if (cancelled) return;
         
        setError(err instanceof ApiError ? err.message : "Failed to load findings.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user, projectId, category, severity, confidence, status, url, rule]);

  useEffect(() => {
    const trimmed = rule.trim();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sync expansion state from URL param
    if (trimmed) setExpandedRule(trimmed);
  }, [rule]);

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  const groups = groupFindings(findings);

  return (
    <div className="mx-auto w-full max-w-6xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <BackToOverview projectId={projectId} />
      <ProjectTabs projectId={projectId} />

      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Findings</h1>
          <p className="mt-1 max-w-2xl text-sm text-zinc-400">
            Every finding is ranked by the ArchitectOS prioritisation model — impact × confidence
            × reach × actionability ÷ risk — with captured evidence for each claim.
          </p>
        </div>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      {/* Filters */}
      <div className="card mb-6 flex flex-wrap items-center gap-2 p-3">
        <FilterSelect value={category} onChange={setCategory} options={CATEGORY_OPTIONS} />
        <FilterSelect value={severity} onChange={setSeverity} options={SEVERITY_OPTIONS} />
        <FilterSelect value={confidence} onChange={setConfidence} options={CONFIDENCE_OPTIONS} />
        <FilterSelect value={status} onChange={setStatus} options={FINDING_STATUS_OPTIONS} />
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Filter by URL"
          aria-label="Filter by URL"
          className="field-input w-44 px-2.5 py-1.5 text-xs"
        />
        <input
          value={rule}
          onChange={(e) => setRule(e.target.value)}
          placeholder="Filter by rule"
          aria-label="Filter by rule"
          className="field-input w-40 px-2.5 py-1.5 text-xs"
        />
        {loading && (
          <span className="ml-auto flex items-center gap-1.5 text-xs text-zinc-500">
            <span
              aria-hidden="true"
              className="size-3 animate-spin rounded-full border border-zinc-700 border-t-brand-400"
            />
            Loading…
          </span>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Grouped list */}
        <section className="card overflow-hidden">
          <div className="card-section px-4 py-2.5 text-xs font-medium text-zinc-500">
            {loading
              ? "Loading…"
              : `${findings.length} finding${findings.length === 1 ? "" : "s"} · ${groups.length} issue type${groups.length === 1 ? "" : "s"} · ranked by priority`}
          </div>
          <ul className="max-h-[34rem] divide-y divide-zinc-800/70 overflow-auto">
            {groups.map((group) => (
              <li key={group.rule}>
                <button
                  type="button"
                  onClick={() => setExpandedRule(expandedRule === group.rule ? null : group.rule)}
                  aria-expanded={expandedRule === group.rule}
                  className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left transition-colors hover:bg-zinc-800/40"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-zinc-200">
                      {getFindingCopy(group.rule).title}
                    </span>
                    <span className="text-xs text-zinc-500">
                      {group.findings.length} page{group.findings.length === 1 ? "" : "s"}
                    </span>
                  </span>
                  <span className={`badge shrink-0 ${SEVERITY_STYLES[group.severity]}`}>
                    {getSeverityLabel(group.severity)}
                  </span>
                </button>
                {expandedRule === group.rule && (
                  <ul className="border-t border-zinc-800/70 bg-zinc-950/30">
                    {group.findings.map((finding) => (
                      <li key={finding.id}>
                        <button
                          type="button"
                          onClick={() => setSelected(finding)}
                          className={`block w-full px-4 py-2 pl-6 text-left transition-colors hover:bg-zinc-800/40 ${
                            selected?.id === finding.id ? "bg-brand-500/10" : ""
                          }`}
                        >
                          <p className="truncate text-xs text-zinc-400">
                            {finding.affected_resource}
                          </p>
                          <p className="mt-0.5 text-xs text-zinc-500">
                            {finding.status.toLowerCase().replace(/_/g, " ")}
                          </p>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
            {findings.length === 0 && !loading && (
              <li className="px-4 py-10 text-center text-sm italic text-zinc-500">
                No findings match these filters.
              </li>
            )}
          </ul>
        </section>

        {/* Evidence panel */}
        <section className="card p-5">
          {!selected ? (
            <div className="flex h-full min-h-40 flex-col items-center justify-center gap-2 text-center">
              <div className="flex size-10 items-center justify-center rounded-full bg-zinc-800/80 text-zinc-500 ring-1 ring-zinc-700/60">
                <svg viewBox="0 0 24 24" fill="none" className="size-5" aria-hidden="true">
                  <path
                    d="M7 4h10a1 1 0 0 1 1 1v14l-6-3-6 3V5a1 1 0 0 1 1-1Z"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
              <p className="text-sm italic text-zinc-500">
                Select a finding to see its evidence.
              </p>
            </div>
          ) : (
            <EvidencePanel finding={selected} />
          )}
        </section>
      </div>
    </div>
  );
}

function groupFindings(
  findings: Finding[]
): { rule: string; severity: RuleSeverity; findings: Finding[] }[] {
  const groups: { rule: string; severity: RuleSeverity; findings: Finding[] }[] = [];
  const index = new Map<string, number>();
  for (const finding of findings) {
    const existing = index.get(finding.rule);
    if (existing === undefined) {
      index.set(finding.rule, groups.length);
      groups.push({ rule: finding.rule, severity: finding.severity, findings: [finding] });
      continue;
    }
    groups[existing].findings.push(finding);
  }
  return groups;
}

function FilterSelect<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T | "";
  onChange: (value: T | "") => void;
  options: { value: T | ""; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as T | "")}
      className="field-input w-auto px-2.5 py-1.5 text-xs"
    >
      {options.map((option) => (
        <option key={option.value || "all"} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}function EvidencePanel({ finding }: { finding: Finding }) {
  return (
    <div className="max-h-[34rem] space-y-4 overflow-auto text-sm">
      <AuditFindingCard
        key={finding.id}
        ruleId={finding.rule}
        observation={finding.observation}
        affectedResource={finding.affected_resource}
        severity={finding.severity}
        category={finding.category}
        status={finding.status}
        evidence={finding.evidence}
        priority={finding.priority}
        priorityFactors={finding.priority_factors}
        findingId={finding.finding_id}
        ruleVersion={finding.rule_version}
        source={`${finding.source} (${finding.source_authority})`}
        expectedMechanism={finding.expected_mechanism}
        risk={finding.risk}
      />
    </div>
  );
}
