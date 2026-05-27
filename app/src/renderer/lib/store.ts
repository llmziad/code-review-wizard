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

export type CommentDecision = "pending" | "approved" | "dismissed";

export type PostingState =
  | { kind: "idle" }
  | { kind: "posting" }
  | { kind: "posted"; url: string }
  | { kind: "error"; message: string };

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

  // Triage state — what's the user's decision on each comment.
  triage: Record<string, CommentDecision>;
  edits: Record<string, string>;
  setDecision: (id: string, decision: CommentDecision) => void;
  setEdit: (id: string, body: string) => void;
  clearEdit: (id: string) => void;

  // Posting state — what happened when "Post" was clicked.
  posting: PostingState;
  setPosting: (state: PostingState) => void;
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
      triage: {},
      edits: {},
      posting: { kind: "idle" },
    }),
  backToInbox: () =>
    set({
      activePrUrl: null,
      bundle: null,
      bundleLoading: false,
      report: null,
      progress: { events: [], status: "idle" },
      triage: {},
      edits: {},
      posting: { kind: "idle" },
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
    set({
      progress: { events: [], status: "running" },
      report: null,
      triage: {},
      edits: {},
      posting: { kind: "idle" },
    }),
  completeReview: (report) =>
    set((s) => {
      // Seed every comment as pending so the triage UI has a starting decision.
      const triage: Record<string, CommentDecision> = {};
      [
        ...(report.comments ?? []),
        ...(report.orphan_comments ?? []),
      ].forEach((c) => {
        if (c.id) triage[c.id] = "pending";
      });
      return {
        progress: { ...s.progress, status: "completed" },
        report,
        triage,
        edits: {},
        posting: { kind: "idle" },
      };
    }),
  failReview: (error) =>
    set((s) => ({ progress: { ...s.progress, status: "error", error } })),
  resetReview: () =>
    set({
      progress: { events: [], status: "idle" },
      report: null,
      triage: {},
      edits: {},
      posting: { kind: "idle" },
    }),

  triage: {},
  edits: {},
  setDecision: (id, decision) =>
    set((s) => ({ triage: { ...s.triage, [id]: decision } })),
  setEdit: (id, body) =>
    set((s) => ({ edits: { ...s.edits, [id]: body } })),
  clearEdit: (id) =>
    set((s) => {
      const { [id]: _removed, ...rest } = s.edits;
      return { edits: rest };
    }),

  posting: { kind: "idle" },
  setPosting: (state) => set({ posting: state }),
}));
