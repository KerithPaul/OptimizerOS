"use client";

import { useMemo, useState } from "react";

/**
 * Colored before/after/diff rendering for the guided fix workflow.
 * Standard diff conventions: removed lines red, added lines green,
 * unchanged lines normal. The diff is computed in the browser from the
 * backend's real pre/post content (snapshot original vs live file) —
 * nothing is synthesized, and no extra dependency is introduced.
 */

type DiffLine = { kind: "add" | "del" | "ctx"; text: string };

/** LCS line diff of the two exact contents, with unchanged-run collapsing. */
export function computeLineDiff(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const n = a.length;
  const m = b.length;

  // LCS table (guard against pathological sizes: skip collapsing above it).
  if (n * m > 4_000_000) {
    return [
      ...a.map((text): DiffLine => ({ kind: "del", text })),
      ...b.map((text): DiffLine => ({ kind: "add", text })),
    ];
  }
  const table: Uint32Array[] = [];
  for (let i = 0; i <= n; i++) table.push(new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i][j] =
        a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const raw: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      raw.push({ kind: "ctx", text: a[i] });
      i++;
      j++;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      raw.push({ kind: "del", text: a[i] });
      i++;
    } else {
      raw.push({ kind: "add", text: b[j] });
      j++;
    }
  }
  while (i < n) raw.push({ kind: "del", text: a[i++] });
  while (j < m) raw.push({ kind: "add", text: b[j++] });

  // Identical contents: show the file as-is (collapsing everything would
  // render a single "unchanged" elision instead of the real code).
  if (raw.every((line) => line.kind === "ctx")) {
    return raw.slice(0, 400);
  }

  // Collapse long unchanged runs the way `git diff` shows context: keep up
  // to 3 context lines around each change, elide the middle.
  const CONTEXT = 3;
  const keep = new Array<boolean>(raw.length).fill(false);
  raw.forEach((line, index) => {
    if (line.kind !== "ctx") {
      for (let k = Math.max(0, index - CONTEXT); k <= Math.min(raw.length - 1, index + CONTEXT); k++) {
        keep[k] = true;
      }
    }
  });
  const out: DiffLine[] = [];
  let runStart = -1;
  for (let index = 0; index <= raw.length; index++) {
    const inRun = index < raw.length && !keep[index];
    if (inRun && runStart < 0) runStart = index;
    if (!inRun && runStart >= 0) {
      const skipped = index - runStart;
      out.push({ kind: "ctx", text: `… ${skipped} unchanged line${skipped === 1 ? "" : "s"} …` });
      runStart = -1;
    }
    if (index < raw.length && keep[index]) out.push(raw[index]);
  }
  return out;
}

const LINE_STYLES: Record<DiffLine["kind"], string> = {
  add: "bg-emerald-500/10 text-emerald-300",
  del: "bg-red-500/10 text-red-300",
  ctx: "text-zinc-400",
};

export function DiffView({ before, after }: { before: string; after: string }) {
  const lines = useMemo(() => computeLineDiff(before, after), [before, after]);
  return (
    <pre className="max-h-72 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950/70 p-2 text-xs leading-relaxed">
      {lines.map((line, index) => (
        <div key={index} className={`px-1 ${LINE_STYLES[line.kind]}`}>
          <span aria-hidden="true" className="mr-1 select-none">
            {line.kind === "add" ? "+" : line.kind === "del" ? "-" : " "}
          </span>
          {line.text || "\u00A0"}
        </div>
      ))}
    </pre>
  );
}

/**
 * Renders the backend's own unified diff (computed with difflib on the full
 * before/after text). Prefer this over `DiffView` for stored runs: their
 * before/after copies are truncated for storage, and diffing two differently
 * truncated strings shows insertions as phantom deletions.
 */
export function UnifiedDiffView({ diff }: { diff: string }) {
  const lines = useMemo(
    () =>
      diff
        .split("\n")
        .filter((line) => !line.startsWith("+++") && !line.startsWith("---"))
        .map((text): DiffLine & { hunk: boolean } => ({
          kind: text.startsWith("+") ? "add" : text.startsWith("-") ? "del" : "ctx",
          text: text.startsWith("+") || text.startsWith("-") ? text.slice(1) : text.replace(/^ /, ""),
          hunk: text.startsWith("@@"),
        })),
    [diff]
  );
  return (
    <pre className="max-h-72 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950/70 p-2 text-xs leading-relaxed">
      {lines.map((line, index) => (
        <div
          key={index}
          className={`px-1 ${line.hunk ? "text-zinc-600" : LINE_STYLES[line.kind]}`}
        >
          <span aria-hidden="true" className="mr-1 select-none">
            {line.hunk ? " " : line.kind === "add" ? "+" : line.kind === "del" ? "-" : " "}
          </span>
          {line.text || "\u00A0"}
        </div>
      ))}
    </pre>
  );
}

function CodePane({ label, content }: { label: string; content: string }) {
  return (
    <div>
      <p className="eyebrow mb-1">{label}</p>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950/70 p-2 text-xs leading-relaxed text-zinc-400">
        {content || "(empty)"}
      </pre>
    </div>
  );
}

/**
 * Before / After / Diff tabs over one file's real pre- and post-fix content.
 * `before` is the immutable snapshot original; `after` is the live file.
 */
export function BeforeAfterDiffTabs({
  before,
  after,
  changed,
}: {
  before: string;
  after: string;
  changed: boolean;
}) {
  const [tab, setTab] = useState<"before" | "after" | "diff">("diff");

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-1.5" role="tablist">
        {(["before", "after", "diff"] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`rounded-lg px-3 py-1.5 text-xs capitalize transition-colors ${
              tab === key
                ? "bg-brand-500/15 font-medium text-brand-300 ring-1 ring-inset ring-brand-500/30"
                : "bg-zinc-800/60 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
            }`}
          >
            {key}
          </button>
        ))}
        {!changed && (
          <span className="badge ml-1 bg-zinc-500/15 text-zinc-400">no changes yet</span>
        )}
      </div>
      {tab === "before" && <CodePane label="Original (immutable snapshot)" content={before} />}
      {tab === "after" && <CodePane label="Current file after the fix" content={after} />}
      {tab === "diff" && <DiffView before={before} after={after} />}
    </div>
  );
}
