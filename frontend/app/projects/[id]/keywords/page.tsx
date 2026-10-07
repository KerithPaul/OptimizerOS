"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ApiError, getKeywordSuggestions } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import type { KeywordSuggestion, KeywordSuggestions } from "@/lib/types";

function formatCtr(ctr: number): string {
  return `${(ctr * 100).toFixed(1)}%`;
}

function filterLabel(filter: string): string {
  if (filter === "position_gte_8") return "position ≥ 8.0";
  if (filter === "ctr_lt_0.02") return "CTR < 2%";
  return filter;
}

export default function KeywordsPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [data, setData] = useState<KeywordSuggestions | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const payload = await getKeywordSuggestions(projectId);
    setData(payload);
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    void load().catch((err) => {
      setError(err instanceof ApiError ? err.message : "Failed to load keyword suggestions.");
    });
  }, [user, load]);

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading...</p>
      </div>
    );
  }

  const suggestions = data?.suggestions ?? [];

  return (
    <div className="mx-auto w-full max-w-3xl flex-1 px-6 py-10">
      <Link href={`/projects/${projectId}`} className="mb-6 inline-block text-sm text-zinc-500 hover:underline">
        &larr; Project
      </Link>
      <h1 className="mb-2 text-lg font-semibold">Keywords</h1>
      <p className="mb-4 text-sm text-zinc-500">
        Suggestions in this pass come from Google Search Console queries already stored for this
        project. Each row is an observed query. Source: gsc.
      </p>
      {data?.note && <p className="mb-4 text-sm text-zinc-600 dark:text-zinc-400">{data.note}</p>}
      <p className="mb-6 text-xs text-zinc-500">
        Keep a query when average position is 8.0 or worse, or CTR is below 2%. Ranked by
        impressions.{" "}
        <Link
          href={`/projects/${projectId}/search-console`}
          className="text-zinc-600 hover:underline dark:text-zinc-300"
        >
          Search Console
        </Link>
      </p>

      {error && <p className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>}

      {data && (
        <p className="mb-4 text-sm text-zinc-600 dark:text-zinc-400">
          {data.connection.display}
          {data.suggestion_count > 0
            ? ` · ${data.suggestion_count} suggestion${data.suggestion_count === 1 ? "" : "s"} from ${data.query_row_count} query row${data.query_row_count === 1 ? "" : "s"}`
            : ""}
        </p>
      )}

      <section className="mb-8 rounded-lg border border-zinc-200 dark:border-zinc-800">
        <h2 className="border-b border-zinc-200 px-4 py-2 text-sm font-semibold dark:border-zinc-800">
          GSC query opportunities
        </h2>
        {suggestions.length === 0 ? (
          <p className="px-4 py-4 text-sm italic text-zinc-400">
            No GSC query opportunities yet. Connect Search Console, then{" "}
            <Link href={`/projects/${projectId}/dashboard`} className="not-italic text-zinc-600 hover:underline dark:text-zinc-300">
              run an audit
            </Link>{" "}
            to store query metrics.
          </p>
        ) : (
          <div className="overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-zinc-500">
                <tr>
                  <th className="px-4 py-2 font-medium">query</th>
                  <th className="px-4 py-2 font-medium">impressions</th>
                  <th className="px-4 py-2 font-medium">clicks</th>
                  <th className="px-4 py-2 font-medium">CTR</th>
                  <th className="px-4 py-2 font-medium">position</th>
                  <th className="px-4 py-2 font-medium">why listed</th>
                  <th className="px-4 py-2 font-medium">source</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
                {suggestions.map((row) => (
                  <SuggestionRow key={row.search_console_row_id ?? row.query} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function SuggestionRow({ row }: { row: KeywordSuggestion }) {
  return (
    <tr>
      <td className="max-w-xs truncate px-4 py-2">{row.query}</td>
      <td className="px-4 py-2">{row.impressions.toLocaleString()}</td>
      <td className="px-4 py-2">{row.clicks}</td>
      <td className="px-4 py-2">{formatCtr(row.ctr)}</td>
      <td className="px-4 py-2">{row.position.toFixed(1)}</td>
      <td className="px-4 py-2">
        <p>{row.reason}</p>
        <p className="text-zinc-500">{row.matched_filters.map(filterLabel).join(" · ")}</p>
      </td>
      <td className="px-4 py-2">{row.source}</td>
    </tr>
  );
}
