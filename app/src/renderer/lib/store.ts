/**
 * Single global store for app-wide UI state.
 *
 * Routing is intentionally state-driven (no react-router) — the app has two
 * top-level views (inbox, detail) and a handful of transient overlays
 * (login, settings). A store is lighter than a router for this shape.
 */

import { create } from "zustand";
import type { ContextBundle, PipelineEvent, ReviewReport } from "@shared/types";

export type ReviewProgress = {
  events: PipelineEvent[];
  status: "idle" | "running" | "completed" | "error";
  error?: string;
};

type AppState = {
  // Which PR is open in the detail view. null = inbox.
  activePrUrl: string | null;
  openPr: (url: string) => void;
  backToInbox: () => void;

  // Context bundle for the active PR (loaded on demand by the detail view).
  bundle: ContextBundle | null;
  bundleLoading: boolean;
  setBundle: (bundle: ContextBundle | null) => void;
  setBundleLoading: (loading: boolean) => void;

  // Latest review run for the active PR.
  progress: ReviewProgress;
  report: ReviewReport | null;
  pushEvent: (event: PipelineEvent) => void;
  startReview: () => void;
  completeReview: (report: ReviewReport) => void;
  failReview: (error: string) => void;
  resetReview: () => void;
};

export const useStore = create<AppState>((set) => ({
  activePrUrl: null,
  openPr: (url) =>
    set({
      activePrUrl: url,
      bundle: null,
      bundleLoading: true,
      report: null,
      progress: { events: [], status: "idle" },
    }),
  backToInbox: () =>
    set({
      activePrUrl: null,
      bundle: null,
      bundleLoading: false,
      report: null,
      progress: { events: [], status: "idle" },
    }),

  bundle: null,
  bundleLoading: false,
  setBundle: (bundle) => set({ bundle, bundleLoading: false }),
  setBundleLoading: (bundleLoading) => set({ bundleLoading }),

  progress: { events: [], status: "idle" },
  report: null,
  pushEvent: (event) =>
    set((s) => ({ progress: { ...s.progress, events: [...s.progress.events, event] } })),
  startReview: () =>
    set({ progress: { events: [], status: "running" }, report: null }),
  completeReview: (report) =>
    set((s) => ({ progress: { ...s.progress, status: "completed" }, report })),
  failReview: (error) =>
    set((s) => ({ progress: { ...s.progress, status: "error", error } })),
  resetReview: () =>
    set({ progress: { events: [], status: "idle" }, report: null }),
}));
