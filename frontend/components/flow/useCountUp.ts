"use client";

import { useEffect, useState } from "react";

/** Animated integer count-up used by score rings. Respects reduced motion via CSS. */
export function useCountUp(target: number, durationMs = 700): number {
  const [value, setValue] = useState(target);

  useEffect(() => {
    if (typeof target !== "number" || Number.isNaN(target)) return;
    // Reduced motion: keep the initialized value, skip the animation.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let raf = 0;
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(target * eased));
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);

  return value;
}
