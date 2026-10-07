"use client";

import { useState, type ReactNode } from "react";
import type { EvidenceRow, FindingStatus, PriorityFactors, RuleCategory } from "@/lib/types";
import {
  buildEvidenceSummary,
  fallbackEvidenceSummary,
  getCategoryLabel,
  getFindingCopy,
  getSeverityLabel,
} from "@/lib/findingMappings";
import {
  CATEGORY_STYLES,
  FINDING_STATUS_STYLES,
  SEVERITY_STYLES,
} from "@/components/flow/badges";

/**
 * User-friendly audit finding card.
 *
 * Presents the five core pieces in the spec's recommended order:
 *   severity + category → title → what was found → why it matters →
 *   recommended action → evidence → affected page → view technical details.
 *
 * Technical data (rule IDs, observed JSON, expected conditions, priority
 * decimals, finding IDs) stays hidden behind "View technical details".
 * Everything user-facing comes from the deterministic mapping in
 * `lib/findingMappings.ts`; the backend contract is untouched.
 */

export interface AuditFindingCardProps {
  /** Backend rule ID, e.g. "SEO-ORPHAN-PAGE-001". Never shown by default. */
  ruleId: string;
  /** Backend observation sentence (technical details + cautious fallback). */
  observation: string;
  /** URL or path where the issue was detected. */
  affectedResource: string;
  severity?: string;
  category?: string;
  status?: FindingStatus | string;
  /** Evidence rows; row 0 is the direct measurement used for the summary. */
  evidence?: EvidenceRow[];
  /** Backend recommendation, shown only when the mapping has no copy. */
  recommendedAction?: string;
  priority?: number;
  priorityFactors?: PriorityFactors | null;
  findingId?: string | null;
  ruleVersion?: number;
  /** Source reference, e.g. "Google Search Central — …" (technical details). */
  source?: string | null;
  /** Vendor-derived mechanism text (technical details). */
  expectedMechanism?: string | null;
  /** Backend risk note (technical details). */
  risk?: string | null;
  /** Compact variant for summary lists (omits why-it-matters and evidence). */
  variant?: "full" | "compact";
  className?: string;
  footer?: ReactNode;
}

export function AuditFindingCard({
  ruleId,
  observation,
  affectedResource,
  severity,
  category,
  status,
  evidence,
  recommendedAction,
  priority,
  priorityFactors,
  findingId,
  ruleVersion,
  source,
  expectedMechanism,
  risk,
  variant = "full",
  className = "",
  footer,
}: AuditFindingCardProps) {
  const copy = getFindingCopy(ruleId);
  const compact = variant === "compact";
  // Cautious language: an interpretive finding leads with the deterministic
  // summary; the backend observation is still available in technical details.
  const summary = copy.summary;

  return (
    <div className={className}>
      <div className="flex flex-wrap items-center gap-2">
        {severity && (
          <span className={`badge ${SEVERITY_STYLES[severity as RuleSeverityKey] ?? SEVERITY_FALLBACK}`}>
            {getSeverityLabel(severity)}
          </span>
        )}
        {category && (
          <span className={`badge ${CATEGORY_STYLES[category as RuleCategory] ?? SEVERITY_FALLBACK}`}>
            {getCategoryLabel(category)}
          </span>
        )}
        {status && (
          <span className={`badge ${FINDING_STATUS_STYLES[status as FindingStatus] ?? SEVERITY_FALLBACK}`}>
            {String(status).toLowerCase().replace(/_/g, " ")}
          </span>
        )}
        {copy.interpretive && (
          <span className="badge bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30">
            needs review
          </span>
        )}
      </div>

      <h3 className="mt-2 text-base font-semibold leading-snug text-zinc-100">{copy.title}</h3>
      <p className="mt-1 text-sm leading-relaxed text-zinc-300">{summary}</p>

      {!compact && (
        <div className="mt-3 space-y-3">
          <section>
            <h4 className="eyebrow">Why it matters</h4>
            <p className="text-sm leading-relaxed text-zinc-300">{copy.whyItMatters}</p>
          </section>
          <section>
            <h4 className="eyebrow">Recommended action</h4>
            <p className="text-sm leading-relaxed text-zinc-300">
              {copy.recommendedAction ?? recommendedAction}
            </p>
          </section>
          <section>
            <h4 className="eyebrow">Evidence</h4>
            <p className="text-sm leading-relaxed text-zinc-300">
              {getEvidenceSummary(ruleId, evidence)}
            </p>
          </section>
        </div>
      )}
      {compact && (
        <p className="mt-2 text-sm leading-relaxed text-zinc-400">
          {copy.recommendedAction ?? recommendedAction}
        </p>
      )}

      <section className="mt-3">
        <h4 className="eyebrow">Affected page</h4>
        <p className="break-all text-xs text-zinc-400">{affectedResource}</p>
      </section>

      {footer}

      <FindingTechnicalDetails
        ruleId={ruleId}
        observation={observation}
        evidence={evidence}
        severity={severity}
        category={category}
        status={status}
        priority={priority}
        priorityFactors={priorityFactors}
        findingId={findingId}
        ruleVersion={ruleVersion}
        source={source}
        expectedMechanism={expectedMechanism}
        risk={risk}
        expectedCondition={evidence?.[0]?.selector ?? null}
        className="mt-3"
      />
    </div>
  );
}

const SEVERITY_FALLBACK = "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30";

type RuleSeverityKey = "critical" | "high" | "medium" | "low";

/** Simple factual evidence line derived deterministically from the measurement. */
export function getEvidenceSummary(ruleId: string, evidence?: EvidenceRow[]): string {
  const observed = evidence?.[0]?.value;
  return buildEvidenceSummary(ruleId, observed) ?? fallbackEvidenceSummary(observed);
}

export interface FindingTechnicalDetailsProps {
  ruleId: string;
  observation: string;
  evidence?: EvidenceRow[];
  severity?: string;
  category?: string;
  status?: FindingStatus | string;
  priority?: number;
  priorityFactors?: PriorityFactors | null;
  findingId?: string | null;
  ruleVersion?: number;
  source?: string | null;
  expectedMechanism?: string | null;
  risk?: string | null;
  expectedCondition?: string | null;
  className?: string;
}

/** Collapsible, visually separated technical section. Hidden by default. */
export function FindingTechnicalDetails({
  ruleId,
  observation,
  evidence,
  severity,
  category,
  status,
  priority,
  priorityFactors,
  findingId,
  ruleVersion,
  source,
  expectedMechanism,
  risk,
  expectedCondition,
  className = "",
}: FindingTechnicalDetailsProps) {
  const [open, setOpen] = useState(false);
  const panelId = `tech-details-${ruleId.replace(/[^a-zA-Z0-9-]/g, "-")}`;
  const rows = evidence ?? [];

  return (
    <div className={className}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        className="inline-flex items-center gap-1 text-xs font-medium text-brand-400 transition-colors hover:text-brand-300"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
          className={`size-3.5 transition-transform ${open ? "rotate-90" : ""}`}
        >
          <path d="m9 6 6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {open ? "Hide technical details" : "View technical details"}
      </button>

      {open && (
        <div
          id={panelId}
          className="mt-2 rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 text-xs leading-relaxed text-zinc-400"
        >
          <dl className="space-y-1.5">
            <DetailTerm term="Rule ID" value={ruleId} mono />
            {ruleVersion !== undefined && <DetailTerm term="Rule version" value={`v${ruleVersion}`} />}
            {expectedCondition && <DetailTerm term="Expected" value={expectedCondition} />}
            <DetailTerm term="Observed" value={observation} />
            {priority !== undefined && (
              <DetailTerm term="Priority" value={priority.toFixed(3)} />
            )}
            {priorityFactors && (
              <DetailTerm
                term="Priority factors"
                value={Object.entries(priorityFactors)
                  .filter(([key]) => key !== "reach_input")
                  .map(([key, value]) => `${key} ${typeof value === "number" ? value.toFixed(2) : value}`)
                  .join(" · ")}
              />
            )}
            {severity && <DetailTerm term="Severity" value={severity} />}
            {category && <DetailTerm term="Category" value={category} />}
            {source && <DetailTerm term="Source" value={source} />}
            {expectedMechanism && <DetailTerm term="Mechanism" value={expectedMechanism} />}
            {risk && <DetailTerm term="Risk" value={risk} />}
            {status && <DetailTerm term="Finding status" value={String(status)} />}
            {findingId && <DetailTerm term="Finding ID" value={findingId} mono />}
          </dl>

          {rows.length > 1 && (
            <div className="mt-3">
              <p className="eyebrow mb-1">Evidence rows ({rows.length})</p>
              <ul className="space-y-1.5">
                {rows.map((row, index) => (
                  <li key={index} className="rounded-md border border-zinc-800 bg-zinc-950/50 p-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium text-zinc-300">{row.source}</span>
                      <span className="badge shrink-0 bg-zinc-500/15 text-zinc-400">{row.confidence}</span>
                    </div>
                    {row.excerpt && <p className="mt-0.5 break-words text-zinc-500">{row.excerpt}</p>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DetailTerm({ term, value, mono = false }: { term: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-wrap gap-x-2">
      <dt className="shrink-0 text-zinc-500">{term}:</dt>
      <dd className={`min-w-0 break-all text-zinc-300 ${mono ? "font-mono text-[11px]" : ""}`}>{value}</dd>
    </div>
  );
}
