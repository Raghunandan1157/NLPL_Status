// Centralized DB / master-file helpers.
//
// This is the single source of truth for master-file (Demand Master, Last
// Month PAR) status and DuckDB sync. The DB Module owns the full management
// UI; EOD and Hourly reuse `useDbStatus` / `summarizeDb` here for their small
// read-only status summaries instead of duplicating the logic.
//
// All calls go to existing source-supported `/eod/*` endpoints.
import { useCallback, useEffect, useState } from "react";
import {
  clearDb as eodClearDb,
  getBackendFilesStatus,
  getDbStatus,
  ingestSingleToDb,
  ingestToDb,
  saveBackendFile,
} from "../eod/api.js";

export { getBackendFilesStatus, getDbStatus };

// The two supported master files. `backendKey` is the type the
// save-backend-file endpoint expects; `ingestKey` is what ingest-single-to-db
// expects; `table` is the DuckDB table name.
export const MASTER_FILES = [
  {
    id: "demandMaster",
    label: "Demand Master",
    backendKey: "masterDemand",
    ingestKey: "demand",
    table: "Demand_Master",
    dbKey: "demandMaster",
  },
  {
    id: "lastMonthPar",
    label: "Last Month PAR",
    backendKey: "lastMonthPar",
    ingestKey: "lastMonth",
    table: "Last_Month_PAR",
    dbKey: "lastMonthPar",
  },
];

/** Fast upload — saves the file only, no DuckDB ingestion (deferred to Save to DB). */
export function uploadMasterFile(backendKey, file) {
  return saveBackendFile(backendKey, file, { ingest: false });
}

/** Ingest a single already-uploaded master file into DuckDB. */
export function ingestMasterFile(ingestKey) {
  return ingestSingleToDb(ingestKey);
}

/** Ingest both master files into DuckDB (Sync DuckDB). */
export const syncDuckDb = () => ingestToDb();

/** Drop the Demand_Master + Last_Month_PAR tables. */
export const clearDuckDb = () => eodClearDb();

/**
 * Shared status hook. Fetches backend-files + db status together and exposes a
 * `refresh` callback. Used by the DB Module (full UI) and by EOD/Hourly (small
 * summaries) so all three stay in sync with one implementation.
 */
export function useDbStatus({ auto = true } = {}) {
  const [backend, setBackend] = useState(null);
  const [db, setDb] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    const [b, d] = await Promise.allSettled([getBackendFilesStatus(), getDbStatus()]);
    setBackend(b.status === "fulfilled" ? b.value : null);
    setDb(d.status === "fulfilled" ? d.value : null);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (auto) refresh();
  }, [auto, refresh]);

  return { backend, db, loading, refresh };
}

/**
 * Derive a compact summary from a {backend, db} status object. Works whether
 * the caller fetched via useDbStatus or holds its own status shape.
 */
export function summarizeDb(status) {
  const backend = status?.backend ?? null;
  const db = status?.db ?? null;

  const files = MASTER_FILES.map((f) => {
    const savedName = backend?.[f.backendKey] || null;
    const meta = backend?.meta?.[f.backendKey] || null;
    const dbInfo = db?.[f.dbKey] || null;
    return {
      ...f,
      saved: Boolean(savedName),
      savedName,
      displayName: meta?.displayName || savedName || null,
      size: meta?.size ?? null,
      modified: meta?.modified ?? null,
      loaded: Boolean(dbInfo?.loaded),
      rowCount: dbInfo?.rowCount ?? 0,
    };
  });

  const allSaved = files.every((f) => f.saved);
  const allLoaded = files.every((f) => f.loaded);
  const anySaved = files.some((f) => f.saved);
  const needsSync = files.some((f) => f.saved && !f.loaded) || !allLoaded;

  return { files, allSaved, allLoaded, anySaved, needsSync, ready: allSaved && allLoaded };
}

/** Navigate to the DB Module page from anywhere (hash-based routing). */
export function openDbModule() {
  window.location.hash = "db";
}
