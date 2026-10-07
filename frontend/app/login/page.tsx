"use client";

/**
 * Sign-in — full-screen split layout.
 *
 * Left (55%): product showcase on the brand panel — deep ink/navy in dark
 * mode (#050816→#0A0F1F), the light theme's deep-navy header family
 * (#123B6D→#0D2F57) in light mode. Grid backdrop, radial glow, feature
 * blocks, and floating dashboard preview cards.
 *
 * Right (45%): centered enterprise auth card. Everything user-facing is
 * themed through the existing design tokens (zinc/brand/accent remap under
 * html.light), so light mode follows the app's light system automatically.
 */

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { ApiError, login } from "@/lib/api";

const REMEMBER_KEY = "architectos_remember_email";

const FEATURES = [
  {
    title: "Evidence-backed audits",
    body: "Crawl your live site and get scored findings for technical SEO, content, AEO, and GEO — every claim backed by captured evidence.",
    icon: (
      <path
        d="m21 21-4.35-4.35M17 10.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0Zm-9.5 0 1.75 1.75L13 8.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    ),
  },
  {
    title: "Agents that plan, not guess",
    body: "Research and optimization agents turn findings into ranked, hypothesised interventions with explicit risk and expected mechanism.",
    icon: (
      <>
        <path
          d="M4 5.5h9M4 11h6.5M4 16.5h9"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <path
          d="m14.5 9.5 2.25 2.25L21 7.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </>
    ),
  },
  {
    title: "Ship changes safely",
    body: "Review diffs, run sandbox validation, publish pull requests, and roll back semantically — down to a single function.",
    icon: (
      <>
        <path
          d="M12 3.5 19 6v5.25c0 4.4-2.9 7.3-7 8.75-4.1-1.45-7-4.35-7-8.75V6l7-2.5Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="m9.25 11.75 2 2 3.5-3.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </>
    ),
  },
];

/* Shared classes (panel sits on the brand navy in both themes) ------------- */

const PANEL_CARD =
  "rounded-2xl border border-white/10 bg-[#0b1020]/90 p-4 shadow-[0_16px_40px_-16px_rgb(0_0_0/0.65)] backdrop-blur-sm " +
  "light:border-[#0d2f57]/25 light:bg-white light:shadow-[0_16px_40px_-18px_rgb(9_30_66/0.4)]";
const PANEL_CARD_LABEL =
  "text-[10px] font-semibold uppercase tracking-widest text-zinc-500 light:text-[#52657a]";
const PANEL_CARD_VALUE = "text-lg font-semibold text-white light:text-[#123b6d]";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Remembered email (client-side convenience only — never the password).
  useEffect(() => {
    try {
      const saved = localStorage.getItem(REMEMBER_KEY);
      if (saved) {
        setEmail(saved);
        setRemember(true);
      }
    } catch {
      /* storage unavailable — ignore */
    }
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setHint(null);
    setSubmitting(true);
    try {
      await login(email, password);
      try {
        if (remember) localStorage.setItem(REMEMBER_KEY, email.trim());
        else localStorage.removeItem(REMEMBER_KEY);
      } catch {
        /* storage unavailable — ignore */
      }
      router.push("/projects");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to reach the API.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
      <div className="grid min-h-[calc(100vh-8.5rem)] items-stretch gap-6 lg:grid-cols-[55fr_45fr]">
        {/* ------------------------------------------------ LEFT: showcase */}
        <section
          aria-hidden="true"
          className="animate-fade-up relative hidden flex-col justify-between overflow-hidden rounded-[20px] border border-zinc-800/80 bg-gradient-to-br from-[#050816] via-[#080c1a] to-[#0a0f1f] p-8 sm:p-10 light:border-[#0d2f57] light:from-[#123b6d] light:via-[#10345f] light:to-[#0d2f57] lg:flex"
        >
          {/* Enterprise grid backdrop */}
          <div
            className="pointer-events-none absolute inset-0 opacity-70 [background-size:32px_32px] bg-[linear-gradient(to_right,rgb(255_255_255/0.05)_1px,transparent_1px),linear-gradient(to_bottom,rgb(255_255_255/0.05)_1px,transparent_1px)]"
          />
          {/* Soft blue-violet radial glows */}
          <div className="pointer-events-none absolute -top-28 -right-24 size-96 rounded-full bg-accent-600/20 blur-3xl light:bg-white/10" />
          <div className="pointer-events-none absolute -bottom-32 -left-20 size-96 rounded-full bg-brand-600/20 blur-3xl light:bg-[#1e4f88]/50" />
          <div className="pointer-events-none absolute top-1/3 left-1/2 size-72 -translate-x-1/2 rounded-full bg-brand-500/10 blur-3xl light:bg-white/5" />

          {/* Logo card */}
          <div className="relative">
            <div className="inline-flex items-center gap-3 rounded-2xl border border-white/10 bg-white/5 px-4 py-3 light:border-white/15 light:bg-white/10">
              <div className="bg-grid flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-accent-600 shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
                <svg viewBox="0 0 24 24" fill="none" className="size-5 text-white">
                  <path
                    d="M4 20h16M6 20V9.5L12 5l6 4.5V20M10 20v-5h4v5"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
              <div>
                <p className="text-sm font-semibold tracking-tight text-white">
                  Architect<span className="text-accent-400 light:text-[#9cc3e8]">OS</span>
                </p>
                <p className="text-[11px] font-medium text-zinc-400 light:text-[#b9cde2]">
                  Enterprise AI platform
                </p>
              </div>
            </div>
          </div>

          {/* Headline + features */}
          <div className="relative max-w-xl">
            <h2 className="text-3xl font-semibold leading-[1.15] tracking-tight text-white xl:text-4xl">
              The operating system for{" "}
              <span className="bg-gradient-to-r from-brand-300 via-accent-400 to-brand-300 bg-clip-text text-transparent light:from-white light:via-[#b9d4ee] light:to-white">
                AI-assisted website improvement.
              </span>
            </h2>
            <p className="mt-4 text-sm leading-relaxed text-zinc-400 light:text-[#b9cde2]">
              Audit, improve, and ship your website with evidence-backed AI agents.
            </p>

            <ul className="mt-10 space-y-6">
              {FEATURES.map((feature) => (
                <li key={feature.title} className="flex items-start gap-4">
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30 light:bg-white/10 light:text-[#b9d4ee] light:ring-white/15">
                    <svg viewBox="0 0 24 24" fill="none" className="size-4.5">
                      {feature.icon}
                    </svg>
                  </span>
                  <div>
                    <p className="text-sm font-semibold text-white">{feature.title}</p>
                    <p className="mt-1 max-w-md text-xs leading-relaxed text-zinc-400 light:text-[#b9cde2]">
                      {feature.body}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          {/* Floating dashboard preview cards */}
          <div className="pointer-events-none relative hidden h-40 xl:block" aria-hidden="true">
            {/* Audit score */}
            <div
              className={`${PANEL_CARD} animate-fade-up absolute left-0 top-0 w-44`}
              style={{ animationDelay: "120ms" }}
            >
              <p className={PANEL_CARD_LABEL}>Audit score</p>
              <div className="mt-2 flex items-center gap-3">
                <svg viewBox="0 0 36 36" className="size-11 -rotate-90">
                  <circle cx="18" cy="18" r="15.5" fill="none" strokeWidth="3.5" className="stroke-white/10 light:stroke-[#e7edf3]" />
                  <circle
                    cx="18"
                    cy="18"
                    r="15.5"
                    fill="none"
                    strokeWidth="3.5"
                    strokeLinecap="round"
                    strokeDasharray="97.4"
                    strokeDashoffset="27.3"
                    className="stroke-brand-400 light:stroke-[#1e4f88]"
                  />
                </svg>
                <div>
                  <p className={PANEL_CARD_VALUE}>72</p>
                  <p className="text-[11px] font-medium text-emerald-400">+6 this week</p>
                </div>
              </div>
            </div>

            {/* SEO issues */}
            <div
              className={`${PANEL_CARD} animate-fade-up absolute right-4 top-10 w-52 rotate-1`}
              style={{ animationDelay: "240ms" }}
            >
              <p className={PANEL_CARD_LABEL}>SEO issues</p>
              <p className={`mt-1.5 ${PANEL_CARD_VALUE}`}>
                38 <span className="text-xs font-normal text-zinc-400 light:text-[#52657a]">open</span>
              </p>
              <div className="mt-2.5 space-y-1.5">
                <div className="h-1.5 w-full rounded-full bg-red-400/80" />
                <div className="h-1.5 w-2/3 rounded-full bg-amber-400/80" />
                <div className="h-1.5 w-1/3 rounded-full bg-zinc-500/60 light:bg-[#cbd5e1]" />
              </div>
            </div>

            {/* Pull requests */}
            <div
              className={`${PANEL_CARD} animate-fade-up absolute bottom-2 left-10 w-48 -rotate-1`}
              style={{ animationDelay: "360ms" }}
            >
              <p className={PANEL_CARD_LABEL}>Pull requests</p>
              <div className="mt-2 flex items-center gap-2 text-xs text-zinc-300 light:text-[#33415c]">
                <svg viewBox="0 0 16 16" fill="currentColor" className="size-3.5">
                  <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
                </svg>
                3 open
                <span className="ml-auto flex items-center gap-1.5 font-medium text-emerald-400">
                  <span className="size-1.5 rounded-full bg-emerald-400" />2 merged
                </span>
              </div>
            </div>

            {/* AI recommendations */}
            <div
              className={`${PANEL_CARD} animate-fade-up absolute -bottom-4 right-0 w-56`}
              style={{ animationDelay: "480ms" }}
            >
              <p className={PANEL_CARD_LABEL}>AI recommendations</p>
              <div className="mt-2 flex items-start gap-2">
                <svg viewBox="0 0 24 24" fill="none" className="mt-0.5 size-4 shrink-0 text-accent-400 light:text-[#1e4f88]">
                  <path
                    d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Zm7 11 .9 2.6L22.5 18l-2.6.9L19 21.5l-.9-2.6L15.5 18l2.6-.9L19 14Z"
                    fill="currentColor"
                  />
                </svg>
                <p className="text-xs leading-relaxed text-zinc-300 light:text-[#33415c]">
                  Prioritize missing H1s on high-traffic pages first
                </p>
              </div>
              <p className="mt-2 text-[11px] font-medium text-brand-300 light:text-[#1e4f88]">
                4 new actions ready
              </p>
            </div>
          </div>
        </section>

        {/* --------------------------------------------------- RIGHT: auth */}
        <section className="flex items-center justify-center">
          <div className="w-full max-w-[460px]">
            {/* Mobile logo (the showcase panel hides below lg) */}
            <div className="mb-8 flex flex-col items-center text-center lg:hidden">
              <div className="bg-grid mb-4 flex size-12 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-accent-600 shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
                <svg viewBox="0 0 24 24" fill="none" className="size-6 text-white" aria-hidden="true">
                  <path
                    d="M4 20h16M6 20V9.5L12 5l6 4.5V20M10 20v-5h4v5"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
              <p className="text-sm font-semibold tracking-tight text-zinc-100">
                Architect<span className="text-accent-400">OS</span>
              </p>
              <p className="mt-1 text-xs text-zinc-500">
                The operating system for AI-assisted website improvement.
              </p>
            </div>

            <div className="card animate-fade-up rounded-[20px] p-7 sm:p-9">
              <h1 className="text-2xl font-semibold tracking-tight text-zinc-100">
                Welcome back
              </h1>
              <p className="mt-1.5 text-sm text-zinc-400">
                Sign in to continue improving your websites.
              </p>

              <form onSubmit={handleSubmit} className="mt-8 space-y-5" noValidate>
                <div>
                  <label className="field-label" htmlFor="email">
                    Email
                  </label>
                  <input
                    id="email"
                    type="email"
                    required
                    autoFocus
                    autoComplete="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@company.com"
                    className="field-input rounded-xl px-3.5 py-2.5"
                  />
                </div>

                <div>
                  <label className="field-label" htmlFor="password">
                    Password
                  </label>
                  <div className="relative">
                    <input
                      id="password"
                      type={showPassword ? "text" : "password"}
                      required
                      autoComplete="current-password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      className="field-input rounded-xl px-3.5 py-2.5 pr-11"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      aria-label={showPassword ? "Hide password" : "Show password"}
                      aria-pressed={showPassword}
                      className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-zinc-500 transition duration-200 hover:text-zinc-300 light:hover:text-[#33415c]"
                    >
                      {showPassword ? (
                        <svg viewBox="0 0 24 24" fill="none" className="size-4.5" aria-hidden="true">
                          <path
                            d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Zm9.5 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z"
                            stroke="currentColor"
                            strokeWidth="1.6"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                          <path d="M4 4l16 16" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                        </svg>
                      ) : (
                        <svg viewBox="0 0 24 24" fill="none" className="size-4.5" aria-hidden="true">
                          <path
                            d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Zm9.5 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z"
                            stroke="currentColor"
                            strokeWidth="1.6"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      )}
                    </button>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <label className="flex cursor-pointer items-center gap-2 text-sm text-zinc-400 light:text-[#52657a]">
                    <input
                      type="checkbox"
                      checked={remember}
                      onChange={(e) => setRemember(e.target.checked)}
                      className="size-4 cursor-pointer rounded border-zinc-700 bg-zinc-900 accent-brand-500"
                    />
                    Remember me
                  </label>
                  <button
                    type="button"
                    onClick={() =>
                      setHint(
                        "Password resets are handled by your administrator (Admin → Users).",
                      )
                    }
                    className="text-sm font-medium text-brand-400 transition duration-200 hover:text-brand-300 light:text-[#1e4f88] light:hover:text-[#123b6d]"
                  >
                    Forgot password?
                  </button>
                </div>

                {hint && !error && (
                  <p className="note-info text-xs" role="status">
                    {hint}
                  </p>
                )}
                {error && (
                  <p role="alert" className="note-error text-sm">
                    {error}
                  </p>
                )}

                <button
                  type="submit"
                  disabled={submitting}
                  className="w-full rounded-xl bg-gradient-to-b from-brand-500 to-brand-600 px-4 py-3 text-sm font-semibold text-white shadow-[0_1px_0_0_rgb(255_255_255/0.2)_inset,0_8px_20px_-8px_rgb(79_70_229/0.6)] transition duration-200 hover:from-brand-400 hover:to-brand-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
                >
                  {submitting ? "Signing in…" : "Sign in"}
                </button>
              </form>
            </div>

            <p className="mt-6 text-center text-xs text-zinc-500">
              Protected by session-based authentication. Access is managed by your
              organization&apos;s administrator.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
