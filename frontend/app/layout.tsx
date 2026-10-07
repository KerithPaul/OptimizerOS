import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Geist, Geist_Mono } from "next/font/google";
import { AppNav } from "@/components/flow/AppNav";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "ArchitectOS",
    template: "%s · ArchitectOS",
  },
  description:
    "ArchitectOS — the operating system for AI-assisted website improvement. Audit, plan, and ship SEO, AEO, and GEO changes with evidence-backed agents.",
};

/* Applies the persisted theme before first paint so there is no light/dark
   flash. Defaults to dark (the product's primary design). */
const THEME_SCRIPT = `try{var t=localStorage.getItem("theme");if(t==="light"){document.documentElement.classList.remove("dark");document.documentElement.classList.add("light")}}catch(e){}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="app-bg flex min-h-full flex-col" suppressHydrationWarning>
        <AppNav />
        <main className="flex flex-1 flex-col">{children}</main>
      </body>
    </html>
  );
}
