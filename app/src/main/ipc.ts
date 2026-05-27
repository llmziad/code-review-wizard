/**
 * IPC handlers — translates renderer calls into sidecar requests.
 *
 * The renderer passes a `requestId` it generated itself; we reuse it as the
 * sidecar's request id so streaming events flow back tagged with the same id
 * the renderer can match against its in-flight requests.
 */

import { ipcMain, type WebContents } from "electron";
import { sidecar, type SidecarEvent } from "./sidecar";

const CHANNEL_CALL = "reviewer:call";
const CHANNEL_EVENT = "reviewer:event";

export function registerIpcHandlers(): void {
  ipcMain.handle(
    CHANNEL_CALL,
    async (event, payload: { method: string; params: object; requestId: string }) => {
      const { method, params, requestId } = payload;
      return sidecar.request(
        method,
        params,
        (sidecarEvent: SidecarEvent) => {
          // Forward the event back to the renderer that issued this call.
          // Tag with `requestId` so the renderer can filter to its in-flight call.
          forwardEvent(event.sender, requestId, sidecarEvent);
        },
        requestId,
      );
    },
  );
}

function forwardEvent(
  sender: WebContents,
  requestId: string,
  event: SidecarEvent,
): void {
  if (sender.isDestroyed()) return;
  sender.send(CHANNEL_EVENT, { requestId, event });
}
