"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { getCurrentUser } from "@/lib/api";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    getCurrentUser()
      .then(() => router.replace("/projects"))
      .catch(() => router.replace("/login"));
  }, [router]);

  return (
    <div className="flex flex-1 items-center justify-center" role="status" aria-label="Loading">
      <div className="flex flex-col items-center gap-3">
        <span
          aria-hidden="true"
          className="size-8 animate-spin rounded-full border-2 border-zinc-700 border-t-brand-500"
        />
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    </div>
  );
}
