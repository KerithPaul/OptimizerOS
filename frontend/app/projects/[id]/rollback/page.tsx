"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  getChangeSet,
  listChangeSets,
  requestRollback,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import type {
  ChangeItem,
  ChangeSetDetail,
  ChangeSetSummary,
  ChangeTransaction,
  RollbackOperation,
  RollbackTargetRequest,
} from "@/lib/types";

const SET_STATUS_STYLES: Record<string, string> = {
  applied: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  partially_rolled_back: "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30",
  rolled_back: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
};

const ITEM_STATUS_STYLES: Record<string, string> = {
  applied: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  rolled_back: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
};

const CATEGORY_SHORTCUTS: { label: string; category: string }[] = [
  { label: "Last SEO change", category: "seo" },
  { label: "Last AEO change", category: "aeo" },
  { label: "Last GEO change", category: "geo" },
];

export default function RollbackPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [changeSets, setChangeSets] = useState<ChangeSetSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ChangeSetDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [operations, setOperations] = useState<Record<string, RollbackOperation>>({});
  const [pending, setPending] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    try {
      const rows = await listChangeSets(projectId);
      setChangeSets(rows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load change sets.");
    }
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void loadList();
  }, [user, loadList]);

  const loadDetail = useCallback(
    async (id: number) => {
      setLoadingDetail(true);
      setError(null);
      try {
        const row = await getChangeSet(projectId, id);
        setDetail(row);
      } catch (err) {
        setDetail(null);
        setError(err instanceof ApiError ? err.message : "Failed to load change set detail.");
      } finally {
        setLoadingDetail(false);
      }
    },
    [projectId]
  );

  useEffect(() => {
    if (!user || selectedId === null) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch detail when selection changes
    void loadDetail(selectedId);
  }, [user, selectedId, loadDetail]);

  const runRollback = useCallback(
    async (key: string, target: RollbackTargetRequest) => {
      setPending(key);
      setError(null);
      try {
        const operation = await requestRollback(projectId, target);
        setOperations((prev) => ({ ...prev, [key]: operation }));
        if (operation.status === "applied") {
          await loadList();
          if (selectedId !== null) await loadDetail(selectedId);
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Rollback request failed.");
      } finally {
        setPending(null);
      }
    },
    [projectId, loadList, loadDetail, selectedId]
  );

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
        <h1 className="text-xl font-semibold tracking-tight">Rollback</h1>
        <p className="mt-1 max-w-3xl text-sm leading-relaxed text-zinc-400">
          Every applied code change is a Change Set made of one or more Change Transactions
          (files), each broken into Change Items (functions, components, or content blocks). Roll
          back a whole set, a single file, or a single item — a low-confidence target stops and
          asks for confirmation instead of overwriting anything.
        </p>
      </div>

      {/* Category shortcuts */}
      <div className="mb-6 flex flex-wrap gap-2">
        {CATEGORY_SHORTCUTS.map((shortcut) => {
          const key = `category:${shortcut.category}`;
          return (
            <button
              key={shortcut.category}
              type="button"
              disabled={pending === key}
              onClick={() => runRollback(key, { category: shortcut.category, confirmed: false })}
              className="btn-secondary px-3 py-1.5 text-xs"
            >
              {pending === key ? "Working…" : shortcut.label}
            </button>
          );
        })}
        {Object.entries(operations)
          .filter(([key]) => key.startsWith("category:"))
          .map(([key, operation]) => (
            <OperationBadge
              key={key}
              operation={operation}
              onConfirm={() => {
                const category = key.replace("category:", "");
                void runRollback(key, { category, confirmed: true });
              }}
            />
          ))}
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-[300px_1fr]">
        {/* Change set list */}
        <ul className="card h-fit divide-y divide-zinc-800/70 overflow-hidden">
          {changeSets.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                onClick={() => setSelectedId(row.id)}
                aria-pressed={selectedId === row.id}
                className={`block w-full px-4 py-3 text-left transition-colors hover:bg-zinc-800/40 ${
                  selectedId === row.id ? "bg-brand-500/10" : ""
                }`}
              >
                <p className="mb-0.5 truncate font-medium text-zinc-200">
                  Change Set #{row.id}
                </p>
                <p className="mb-1.5 truncate text-xs text-zinc-500">{row.objective}</p>
                <span className={`badge ${SET_STATUS_STYLES[row.status] ?? ""}`}>
                  {row.status}
                </span>
              </button>
            </li>
          ))}
          {changeSets.length === 0 && (
            <li className="px-4 py-10 text-center text-sm italic text-zinc-500">
              No applied changes yet.
            </li>
          )}
        </ul>

        {/* Detail */}
        <div>
          {loadingDetail && <p className="text-sm text-zinc-500">Loading…</p>}
          {!loadingDetail && selectedId !== null && detail && (
            <ChangeSetView
              detail={detail}
              operations={operations}
              pending={pending}
              onRollback={runRollback}
            />
          )}
          {selectedId === null && (
            <p className="text-sm italic text-zinc-500">Select a change set on the left.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function OperationBadge({
  operation,
  onConfirm,
}: {
  operation: RollbackOperation;
  onConfirm: () => void;
}) {
  if (operation.status === "applied") {
    return (
      <span className="badge bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
        rolled back
      </span>
    );
  }
  if (operation.status === "failed") {
    return (
      <span className="badge bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30">
        failed: {operation.result_detail}
      </span>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-xs text-amber-300">
      <span>
        confidence {Math.round(operation.confidence * 100)}% —{" "}
        {operation.confidence_reasons_json.join("; ")}
      </span>
      <button
        type="button"
        onClick={onConfirm}
        className="rounded-md bg-amber-500/20 px-2 py-0.5 font-medium text-amber-200 transition-colors hover:bg-amber-500/30"
      >
        Confirm anyway
      </button>
    </div>
  );
}

function ChangeSetView({
  detail,
  operations,
  pending,
  onRollback,
}: {
  detail: ChangeSetDetail;
  operations: Record<string, RollbackOperation>;
  pending: string | null;
  onRollback: (key: string, target: RollbackTargetRequest) => void;
}) {
  const setKey = `set:${detail.id}`;
  return (
    <div className="space-y-4">
      <div className="card p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-zinc-100">Change Set #{detail.id}</h2>
          <span className={`badge ${SET_STATUS_STYLES[detail.status] ?? ""}`}>
            {detail.status}
          </span>
        </div>
        <p className="mb-1.5 text-xs leading-relaxed text-zinc-400">
          <span className="text-zinc-500">Objective:</span> {detail.objective}
        </p>
        <p className="mb-1.5 break-all text-xs leading-relaxed text-zinc-400">
          <span className="text-zinc-500">Findings:</span> {detail.finding_ids_json.join(", ")}
        </p>
        <p className="mb-1.5 break-all text-xs leading-relaxed text-zinc-400">
          <span className="text-zinc-500">Affected resources:</span>{" "}
          {detail.affected_resources_json.join(", ")}
        </p>
        <p className="mb-3 text-xs leading-relaxed text-zinc-400">
          <span className="text-zinc-500">Risk:</span> {detail.risk}
        </p>
        {detail.status === "applied" && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={pending === setKey}
              onClick={() => onRollback(setKey, { change_set_id: detail.id, confirmed: false })}
              className="btn-danger"
            >
              {pending === setKey ? "Working…" : "Roll back entire Change Set"}
            </button>
            {operations[setKey] && (
              <OperationBadge
                operation={operations[setKey]}
                onConfirm={() => onRollback(setKey, { change_set_id: detail.id, confirmed: true })}
              />
            )}
          </div>
        )}
      </div>

      {detail.transactions.map((transaction) => (
        <TransactionView
          key={transaction.id}
          transaction={transaction}
          operations={operations}
          pending={pending}
          onRollback={onRollback}
        />
      ))}
    </div>
  );
}

function TransactionView({
  transaction,
  operations,
  pending,
  onRollback,
}: {
  transaction: ChangeTransaction;
  operations: Record<string, RollbackOperation>;
  pending: string | null;
  onRollback: (key: string, target: RollbackTargetRequest) => void;
}) {
  const txKey = `transaction:${transaction.id}`;
  return (
    <div className="card p-5">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="break-all font-mono text-xs text-zinc-300">{transaction.resource}</span>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`badge ${ITEM_STATUS_STYLES[transaction.status] ?? ""}`}>
            {transaction.status}
          </span>
          <span className="text-xs text-zinc-500">reason: {transaction.reason}</span>
        </div>
      </div>
      {transaction.status === "applied" && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={pending === txKey}
            onClick={() =>
              onRollback(txKey, { change_transaction_id: transaction.id, confirmed: false })
            }
            className="btn-secondary px-2.5 py-1 text-xs"
          >
            {pending === txKey ? "Working…" : "Roll back this file"}
          </button>
          {operations[txKey] && (
            <OperationBadge
              operation={operations[txKey]}
              onConfirm={() =>
                onRollback(txKey, { change_transaction_id: transaction.id, confirmed: true })
              }
            />
          )}
        </div>
      )}
      <ul className="space-y-1.5">
        {transaction.items.map((item) => (
          <ItemRow
            key={item.id}
            item={item}
            operations={operations}
            pending={pending}
            onRollback={onRollback}
          />
        ))}
        {transaction.items.length === 0 && (
          <li className="text-xs italic text-zinc-500">No sub-file items recorded.</li>
        )}
      </ul>
    </div>
  );
}

function ItemRow({
  item,
  operations,
  pending,
  onRollback,
}: {
  item: ChangeItem;
  operations: Record<string, RollbackOperation>;
  pending: string | null;
  onRollback: (key: string, target: RollbackTargetRequest) => void;
}) {
  const itemKey = `item:${item.id}`;
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-2.5 py-1.5 text-xs">
      <span className="text-zinc-300">
        <span className="text-zinc-500">{item.kind}</span>{" "}
        {item.symbol_name ?? <span className="italic text-zinc-500">(whole file)</span>}
      </span>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`badge ${ITEM_STATUS_STYLES[item.status] ?? ""}`}>{item.status}</span>
        {item.status === "applied" && item.kind !== "file" && (
          <button
            type="button"
            disabled={pending === itemKey}
            onClick={() => onRollback(itemKey, { change_item_id: item.id, confirmed: false })}
            className="btn-secondary px-2 py-0.5 text-xs"
          >
            {pending === itemKey ? "Working…" : "Roll back only this"}
          </button>
        )}
        {operations[itemKey] && (
          <OperationBadge
            operation={operations[itemKey]}
            onConfirm={() => onRollback(itemKey, { change_item_id: item.id, confirmed: true })}
          />
        )}
      </div>
    </li>
  );
}
