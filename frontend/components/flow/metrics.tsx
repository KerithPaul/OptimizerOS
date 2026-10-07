"use client";

import { useCountUp } from "./useCountUp";

export type ScoreTone = "good" | "warn" | "bad";

export function scoreTone(percentage: number): ScoreTone {
  if (percentage >= 80) return "good";
  if (percentage >= 50) return "warn";
  return "bad";
}

export const SCORE_TONE_TEXT: Record<ScoreTone, string> = {
  good: "text-emerald-400",
  warn: "text-amber-400",
  bad: "text-red-400",
};

export const SCORE_TONE_STROKE: Record<ScoreTone, string> = {
  good: "stroke-emerald-500",
  warn: "stroke-amber-500",
  bad: "stroke-red-500",
};

export const SCORE_TONE_BAR: Record<ScoreTone, string> = {
  good: "bg-gradient-to-r from-emerald-500 to-emerald-400",
  warn: "bg-gradient-to-r from-amber-500 to-amber-400",
  bad: "bg-gradient-to-r from-red-500 to-red-400",
};

export function toneFor(value: number, maxValue: number): ScoreTone {
  return scoreTone(maxValue > 0 ? (value / maxValue) * 100 : 0);
}

export function percentage(value: number, maxValue: number): number {
  return maxValue > 0 ? Math.round((value / maxValue) * 100) : 0;
}

/* -------------------------------------------------------------------------- */
/* ScoreRing — animated circular score for the overall audit result.          */
/* -------------------------------------------------------------------------- */
export function ScoreRing({
  percentage: target,
  tone,
  size = "md",
}: {
  percentage: number;
  tone: ScoreTone;
  size?: "md" | "lg";
}) {
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const animated = useCountUp(Math.max(0, Math.min(100, target)));
  const box = size === "lg" ? "size-40" : "size-32";

  return (
    <div className={`relative flex ${box} shrink-0 items-center justify-center`}>
      <svg viewBox="0 0 120 120" className={`${box} -rotate-90`} aria-hidden="true">
        <circle
          cx="60"
          cy="60"
          r={radius}
          fill="none"
          strokeWidth="10"
          className="stroke-zinc-800"
        />
        <circle
          cx="60"
          cy="60"
          r={radius}
          fill="none"
          strokeWidth="10"
          strokeLinecap="round"
          className={`${SCORE_TONE_STROKE[tone]} transition-[stroke-dashoffset] duration-700 ease-out`}
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - animated / 100)}
        />
      </svg>
      <div className="absolute text-center">
        <span
          className={`block tabular-nums ${
            size === "lg" ? "text-4xl" : "text-3xl"
          } font-bold ${SCORE_TONE_TEXT[tone]}`}
        >
          {animated}
        </span>
        <span className="text-[11px] uppercase tracking-wider text-zinc-500">overall</span>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* ScoreMeter — slim gradient bar for category scores.                        */
/* -------------------------------------------------------------------------- */
export function ScoreMeter({ percentage: p, tone }: { percentage: number; tone: ScoreTone }) {
  return (
    <div
      role="progressbar"
      aria-valuenow={p}
      aria-valuemin={0}
      aria-valuemax={100}
      className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800"
    >
      <div
        className={`h-full rounded-full ${SCORE_TONE_BAR[tone]} transition-[width] duration-700 ease-out`}
        style={{ width: `${p}%` }}
      />
    </div>
  );
}
