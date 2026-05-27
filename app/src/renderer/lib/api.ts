/**
 * Typed API surface — every sidecar method as a TypeScript function with
 * payload types pulled from the auto-generated schema.
 *
 * If a method's contract changes on the Python side, `npm run generate:types`
 * refreshes `@shared/types.ts` and this file's call sites become red until
 * they're updated. That's the whole point of the schema export.
 */

import type {
  ContextBundle,
  DeviceFlowChallenge,
  PipelineEvent,
  PRSummary,
  ReviewReport,
  StoredAuth,
} from "@shared/types";
import { call, callWithEvents } from "./sidecar-client";

// --- Whoami / auth ---

export type WhoamiResult = {
  login: string;
  id: number | null;
  name: string | null;
  html_url: string | null;
  token_source: "stored" | "env_or_gh";
};

export type ListPrsParams = { filter?: "all" | "review-requested" | "authored" | "assigned"; limit?: number };
export type ListPrsResult = {
  review_requested?: PRSummary[];
  authored?: PRSummary[];
  assigned?: PRSummary[];
};

export type ReviewPrResult = { run_id: string; report: ReviewReport };

export const api = {
  whoami: () => call<WhoamiResult>("whoami"),
  logout: () => call<{ removed: boolean }>("logout"),

  login: {
    pat: (token: string) => call<StoredAuth>("login_pat", { token }),
    deviceStart: () => call<DeviceFlowChallenge>("login_device_start"),
    devicePoll: (
      challenge: DeviceFlowChallenge,
      onProgress?: (event: { event: string; [k: string]: unknown }) => void,
    ) =>
      callWithEvents<StoredAuth>(
        "login_device_poll",
        { challenge },
        onProgress ?? (() => {}),
      ),
  },

  listPrs: (params: ListPrsParams = { filter: "all", limit: 20 }) =>
    call<ListPrsResult>("list_prs", params),

  inspectPr: (url: string) => call<ContextBundle>("inspect_pr", { url }),

  /** Streaming: `onEvent` fires for every PipelineEvent; promise resolves with the final report. */
  reviewPr: (url: string, onEvent: (event: PipelineEvent) => void) =>
    callWithEvents<ReviewPrResult, PipelineEvent>("review_pr", { url }, onEvent),

  getRun: (runId: string) => call<ReviewReport>("get_run", { run_id: runId }),

  postReview: (params: { run_id: string; comment_ids?: string[]; edits?: Record<string, string> }) =>
    call<{ review_url: string }>("post_review", params),

  dismissComments: (params: { run_id: string; comment_ids: string[]; reason?: string }) =>
    call<{ dismissed: number }>("dismiss_comments", params),
} as const;
