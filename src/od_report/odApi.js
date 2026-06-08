// OD Report backend calls. Served by the migrated `blueprints.od_report`
// blueprint mounted at /od-report. The main /upload endpoint streams its own
// SSE step events back over a multipart POST.
import { API_BASE, requestJson } from "../lib/apiClient.js";

export const checkOd = () => requestJson("/od-report/check-od");
export const checkIns = () => requestJson("/od-report/check-ins");

function uploadStaged(path, file) {
  const fd = new FormData();
  fd.append("file", file);
  return requestJson(path, { method: "POST", body: fd });
}
export const uploadOd = (file) => uploadStaged("/od-report/upload-od", file);
export const uploadIns = (file) => uploadStaged("/od-report/upload-ins", file);

/**
 * POST the PAR file and consume the OD Report SSE step stream. Calls
 * onEvent(parsed) for each `data: {...}` event (step / done / error).
 */
export async function processOd(file, onEvent, { signal } = {}) {
  const fd = new FormData();
  fd.append("file", file);

  let res;
  try {
    res = await fetch(`${API_BASE}/od-report/upload`, { method: "POST", body: fd, signal });
  } catch {
    throw new Error("Cannot reach the backend. Make sure it is running (npm run dev).");
  }
  if (!res.ok || !res.body) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || payload.message || `Request failed (${res.status})`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      try {
        onEvent(JSON.parse(raw));
      } catch {
        onEvent({ message: raw });
      }
    }
  }
}
