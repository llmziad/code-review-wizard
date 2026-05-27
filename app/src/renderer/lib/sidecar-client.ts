/**
 * Renderer-side wrapper over the preload bridge.
 *
 * Two functions cover every sidecar call:
 *   call(method, params)               — non-streaming
 *   callWithEvents(method, params, onEvent) — streaming
 *
 * Higher-level typed API lives in `./api.ts` and composes these.
 */

import type { SidecarEventPayload } from "../../preload";

type EventConsumer = (event: SidecarEventPayload["event"]) => void;

// Single global router. The preload's onEvent fires for every event; we
// dispatch to the right consumer by requestId.
const consumers = new Map<string, EventConsumer>();

let listenerInstalled = false;
function ensureListener(): void {
  if (listenerInstalled) return;
  window.reviewer.onEvent(({ requestId, event }) => {
    consumers.get(requestId)?.(event);
  });
  listenerInstalled = true;
}

export async function call<T>(method: string, params: object = {}): Promise<T> {
  ensureListener();
  const requestId = window.reviewer.newRequestId();
  return window.reviewer.invoke<T>(method, params, requestId);
}

/**
 * `E` defaults to the wire-level event shape. Callers that know the concrete
 * event type for a given method (e.g. `PipelineEvent` for review_pr) can
 * narrow it via the generic parameter — runtime is the same, just typed.
 */
export async function callWithEvents<T, E = SidecarEventPayload["event"]>(
  method: string,
  params: object,
  onEvent: (event: E) => void,
): Promise<T> {
  ensureListener();
  const requestId = window.reviewer.newRequestId();
  consumers.set(requestId, onEvent as EventConsumer);
  try {
    return await window.reviewer.invoke<T>(method, params, requestId);
  } finally {
    consumers.delete(requestId);
  }
}
