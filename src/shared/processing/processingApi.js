// Shared client helpers for the generic process-job lifecycle (status +
// cancellation). The actual /process call stays module-specific (each module
// posts its own files); these endpoints are the common job-control surface.
import { apiUrl, requestJson } from "../../lib/apiClient.js";

/** Generate a client-side process id sent to the backend as `processId` so a
 *  later cancel call can target this exact run. */
export function newProcessId() {
  try {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  } catch {
    /* fall through */
  }
  return `job-${Date.now()}-${Math.floor(Math.random() * 1e9).toString(36)}`;
}

/** Ask the backend to cancel a running job (cooperative — takes effect at the
 *  next processing phase boundary). */
export function cancelProcess(module, processId) {
  return requestJson(`/api/${module}/process/${encodeURIComponent(processId)}/cancel`, {
    method: "POST",
  });
}

/** Best-effort cancel that works during page unload (refresh/close). */
export function cancelProcessBeacon(module, processId) {
  try {
    const url = apiUrl(`/api/${module}/process/${encodeURIComponent(processId)}/cancel`);
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([], { type: "text/plain" }));
      return true;
    }
    // Fallback: fire-and-forget keepalive fetch.
    fetch(url, { method: "POST", keepalive: true }).catch(() => {});
    return true;
  } catch {
    return false;
  }
}

export function getProcessStatus(module, processId) {
  return requestJson(`/api/${module}/process/${encodeURIComponent(processId)}/status`);
}
