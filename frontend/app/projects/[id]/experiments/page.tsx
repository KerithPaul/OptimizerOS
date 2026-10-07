"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  ApiError,
  createExperiment,
  getChangeSet,
  listChangeSets,
  listExperiments,
  measureExperimentTreatment,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import { BackToOverview } from "@/components/flow/ui";
import type {
  ChangeSetDetail,
  ChangeSetSummary,
  Experiment,
  ExperimentType,
} from "@/lib/types";

const TYPES: { value: ExperimentType; label: string }[] = [
  { value: "title", label: "Title" },
  { value: "faq", label: "FAQ" },
  { value: "content_restructure", label: "Content restructure" },
  { value: "schema", label: "Schema" },
  { value: "internal_links", label: "Internal links" },
];

export default function ExperimentsPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [changeSets, setChangeSets] = useState<ChangeSetSummary[]>([]);
  const [selected, setSelected] = useState<Experiment | null>(null);
  const [changeSet, setChangeSet] = useState<ChangeSetDetail | null>(null);
  const [hypothesis, setHypothesis] = useState("");
  const [experimentType, setExperimentType] = useState<ExperimentType>("title");
  const [changeSetId, setChangeSetId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [rows, sets] = await Promise.all([
      listExperiments(projectId),
      listChangeSets(projectId),
    ]);
    setExperiments(rows);
    setChangeSets(sets);
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void load().catch((err) => {
      setError(err instanceof ApiError ? err.message : "Failed to load experiments.");
    });
  }, [user, load]);

  useEffect(() => {
    if (!selected?.change_set_id) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reset when selection has no change set
      setChangeSet(null);
      return;
    }
    void getChangeSet(projectId, selected.change_set_id)
      .then(setChangeSet)
      .catch(() => setChangeSet(null));
  }, [projectId, selected]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setPending("create");
    try {
      const row = await createExperiment(projectId, {
        hypothesis,
        experiment_type: experimentType,
        change_set_id: changeSetId ? Number(changeSetId) : undefined,
      });
      setHypothesis("");
      await load();
      setSelected(row);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create experiment.");
    } finally {
      setPending(null);
    }
  }

  async function onMeasure(row: Experiment) {
    setError(null);
    setPending(`measure-${row.id}`);
    try {
      const updated = await measureExperimentTreatment(projectId, row.id);
      await load();
      setSelected(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to record treatment metrics.");
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

  return (
    <div className="mx-auto w-full max-w-5xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <BackToOverview projectId={projectId} />
      <ProjectTabs projectId={projectId} />

      <div className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight">Experiments</h1>
        <p className="mt-1 max-w-2xl text-sm leading-relaxed text-zinc-400">
          Hypothesis → Baseline → Change → Validation → Post-change measurement → Result. Metric
          movement is correlation, not proof that the change caused it.
        </p>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      {/* Create experiment */}
      <form onSubmit={onCreate} className="card card-section mb-8 p-5">
        <label className="flex flex-col gap-1 text-xs text-zinc-500">
          Hypothesis
          <input
            value={hypothesis}
            onChange={(event) => setHypothesis(event.target.value)}
            className="field-input mt-1"
          />
        </label>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-zinc-500">
            Type
            <select
              value={experimentType}
              onChange={(event) => setExperimentType(event.target.value as ExperimentType)}
              className="field-input mt-1"
            >
              {TYPES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-zinc-500">
            Change Set (optional)
            <select
              value={changeSetId}
              onChange={(event) => setChangeSetId(event.target.value)}
              className="field-input mt-1"
            >
              <option value="">None</option>
              {changeSets.map((row) => (
                <option key={row.id} value={row.id}>
                  #{row.id} {row.objective}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={!hypothesis || pending === "create"}
            className="btn-primary"
          >
            Create experiment
          </button>
        </div>
      </form>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Experiment list */}
        <section className="card h-fit overflow-hidden">
          <ul className="divide-y divide-zinc-800/70">
            {experiments.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => setSelected(row)}
                  aria-pressed={selected?.id === row.id}
                  className={`block w-full px-4 py-3 text-left text-sm transition-colors hover:bg-zinc-800/40 ${
                    selected?.id === row.id ? "bg-brand-500/10" : ""
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium capitalize text-zinc-200">
                      {row.experiment_type.replace(/_/g, " ")}
                    </span>
                    <span className="text-xs text-zinc-500">{row.status}</span>
                  </div>
                  <p className="mt-1 line-clamp-1 text-xs text-zinc-500">{row.hypothesis}</p>
                </button>
              </li>
            ))}
            {experiments.length === 0 && (
              <li className="px-4 py-10 text-center text-sm italic text-zinc-500">
                No experiments yet.
              </li>
            )}
          </ul>
        </section>

        {/* Detail */}
        <section className="card p-5">
          {!selected ? (
            <p className="text-sm italic text-zinc-500">Select an experiment.</p>
          ) : (
            <ExperimentDetail
              experiment={selected}
              changeSet={changeSet}
              pending={pending}
              onMeasure={() => void onMeasure(selected)}
            />
          )}
        </section>
      </div>
    </div>
  );
}

function ExperimentDetail({
  experiment,
  changeSet,
  pending,
  onMeasure,
}: {
  experiment: Experiment;
  changeSet: ChangeSetDetail | null;
  pending: string | null;
  onMeasure: () => void;
}) {
  const result = experiment.result;
  return (
    <div className="space-y-4 text-sm">
      <p className="text-xs text-zinc-400">
        <span className="capitalize">{experiment.status.replace(/_/g, " ")}</span> · causation:{" "}
        {experiment.causation}
      </p>
      <p className="note-warning text-xs">{experiment.causation_note}</p>
      <div>
        <p className="eyebrow">Hypothesis</p>
        <p className="leading-relaxed text-zinc-300">{experiment.hypothesis}</p>
      </div>
      <MetricsBlock title="Baseline" metrics={experiment.baseline_metrics} />
      <MetricsBlock title="Treatment" metrics={experiment.treatment_metrics} />
      {changeSet && (
        <MetricsBlock title="Change Set baseline" metrics={changeSet.baseline_metrics_json} />
      )}
      {changeSet && (
        <MetricsBlock title="Change Set treatment" metrics={changeSet.treatment_metrics_json} />
      )}
      {result && (
        <div>
          <p className="eyebrow">
            Observed delta (not a causal result)
            {experiment.confidence != null
              ? ` · completeness ${experiment.confidence.toFixed(2)}`
              : ""}
          </p>
          <pre className="mt-1 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950/70 p-3 text-[11px] text-zinc-400">
            {JSON.stringify(result, null, 2)}
          </pre>
        </div>
      )}
      <button
        type="button"
        onClick={onMeasure}
        disabled={pending === `measure-${experiment.id}`}
        className="btn-secondary"
      >
        {pending === `measure-${experiment.id}` ? "Measuring…" : "Record post-change measurement"}
      </button>
    </div>
  );
}

function MetricsBlock({
  title,
  metrics,
}: {
  title: string;
  metrics: Record<string, unknown> | null;
}) {
  if (!metrics) {
    return (
      <div>
        <p className="eyebrow">{title}</p>
        <p className="italic text-zinc-500">Not recorded yet.</p>
      </div>
    );
  }
  const inner = (
    metrics.metrics && typeof metrics.metrics === "object" ? metrics.metrics : {}
  ) as Record<string, { status?: string; value?: unknown; detail?: string }>;
  const note = typeof metrics.causation_note === "string" ? metrics.causation_note : null;
  return (
    <div>
      <p className="eyebrow">{title}</p>
      {note && <p className="mb-1 text-[11px] text-zinc-500">{note}</p>}
      <dl className="grid grid-cols-2 gap-2 text-xs">
        {Object.entries(inner).map(([key, item]) => (
          <div
            key={key}
            className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-2.5 py-1.5"
          >
            <dt className="text-zinc-500">{key}</dt>
            <dd className="text-zinc-300">
              {item && typeof item === "object" && "status" in item && item.status === "unavailable"
                ? `unavailable${item.detail ? ` (${item.detail})` : ""}`
                : String(
                    item && typeof item === "object" && "value" in item ? item.value : item
                  )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
