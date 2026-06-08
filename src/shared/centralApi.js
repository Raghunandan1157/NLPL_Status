// Centralized, cross-module API wrappers.
//
//  * Mail login/config is app-level (/api/email/*) — one Gmail login shared by
//    every module that sends mail.
//  * WhatsApp session + contacts are app-level (/api/whatsapp/*) — login once
//    (scan the QR) and every module shares the session; only the file sent is
//    module-specific.
//  * Report history uses the generic /api/reports/<module>/* trio.
import { apiUrl, requestJson } from "../lib/apiClient.js";

export { apiUrl };

/* ----------------------------------------------------- centralized Gmail */
export const emailGetConfig = () => requestJson("/api/email/config");
export function emailLogin({ user, appPassword, host = "smtp.gmail.com", port = 587 }) {
  return requestJson("/api/email/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user, appPassword, host, port }),
  });
}
export const emailLogout = () => requestJson("/api/email/logout", { method: "POST" });

/* -------------------------------------------------- centralized WhatsApp */
export const whatsappContactsGet = () => requestJson("/api/whatsapp/contacts");
export function whatsappContactsSave(contacts) {
  return requestJson("/api/whatsapp/contacts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contacts }),
  });
}
export const whatsappOpen = () => requestJson("/api/whatsapp/open", { method: "POST" });
export function whatsappSend(bundlePath, filename) {
  return requestJson("/api/whatsapp/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bundle_path: bundlePath, filename }),
  });
}

/* -------------------------------------- generic per-module report history */
export function moduleSnapshotReports(module, date, time = "") {
  return requestJson(`/api/reports/${module}/snapshot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date, time }),
  });
}
export const moduleReportArchiveList = (module) =>
  requestJson(`/api/reports/${module}/archive`);
export const moduleReportArchiveFileUrl = (module, date, run, type) =>
  apiUrl(
    `/api/reports/${module}/file?date=${encodeURIComponent(date)}` +
      `&run=${encodeURIComponent(run)}&type=${encodeURIComponent(type)}`
  );
