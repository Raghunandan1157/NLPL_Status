// Central HTTP client. The Flask backend runs on its own origin (default
// http://127.0.0.1:5055); override with VITE_EOD_API_BASE at build/dev time.
export const API_BASE = import.meta.env.VITE_EOD_API_BASE || "http://127.0.0.1:5055";

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

/** JSON request helper. Throws Error(message) on non-2xx using the server's
 *  error/suggestion/message fields when present. */
export async function requestJson(path, options) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, options);
  } catch (networkErr) {
    throw new Error(
      "Cannot reach the backend. Make sure it is running (npm run dev)."
    );
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const msg = payload.error || payload.message || `Request failed (${response.status})`;
    const err = new Error(payload.suggestion ? `${msg} — ${payload.suggestion}` : msg);
    err.status = response.status;
    err.payload = payload;
    throw err;
  }
  return payload;
}

/**
 * POST a body and consume a Server-Sent-Events response stream (used by
 * /eod/send-batch-email which streams progress as `data: {...}` lines).
 * Calls onEvent(parsedObject) for each event. Resolves when the stream ends.
 */
export async function postSseStream(path, body, onEvent, { signal } = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || payload.message || `Request failed (${response.status})`);
  }
  const reader = response.body.getReader();
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
