"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { jobEventsUrl } from "@/lib/api";
import type { JobEvent } from "@/lib/types";

/**
 * Subscribes to the SSE stream for a backend job and tracks its latest event.
 * Mirrors the existing per-page EventSource behavior in one reusable hook.
 */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set(["succeeded", "failed", "cancelled"]);

export function useJobWatcher() {
  const [event, setEvent] = useState<JobEvent | null>(null);
  const [running, setRunning] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);

  const watch = useCallback((jobId: number, onSucceeded?: () => void) => {
    sourceRef.current?.close();
    setEvent(null);
    setRunning(true);
    const source = new EventSource(jobEventsUrl(jobId), { withCredentials: true });
    sourceRef.current = source;
    source.onmessage = (msg) => {
      let payload: JobEvent;
      try {
        payload = JSON.parse(msg.data) as JobEvent;
      } catch {
        return;
      }
      setEvent(payload);
      if (TERMINAL_STATUSES.has(payload.job_status)) {
        source.close();
        setRunning(false);
        if (payload.job_status === "succeeded") onSucceeded?.();
      }
    };
    source.onerror = () => {
      source.close();
      setRunning(false);
      // The stream can also end without a terminal frame having arrived —
      // a server restart or the tab suspending. Drop the last non-terminal
      // frame so the panel cannot freeze on a stale "running"/"queued".
      setEvent((prev) => (prev && !TERMINAL_STATUSES.has(prev.job_status) ? null : prev));
    };
  }, []);

  const stop = useCallback(() => {
    sourceRef.current?.close();
    setRunning(false);
    // Same guard as onerror: stop() can fire before the terminal event
    // landed (e.g. the user cancelled the job). A frozen non-terminal
    // frame would leave the panel claiming the job is still going.
    setEvent((prev) => (prev && !TERMINAL_STATUSES.has(prev.job_status) ? null : prev));
  }, []);

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
    };
  }, []);

  return { event, running, watch, stop, setEvent };
}
