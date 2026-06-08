import { useState } from "react";
import { Database, FileSpreadsheet, UploadCloud } from "lucide-react";
import { Button, FileDrop, useToast } from "../../components/ui.jsx";
import { fileSizeMB } from "../../lib/format.js";
import { ingestMasterFile, uploadMasterFile } from "../dbApi.js";

function formatWhen(epochSeconds) {
  if (!epochSeconds) return null;
  try {
    return new Date(epochSeconds * 1000).toLocaleString();
  } catch {
    return null;
  }
}

/**
 * One master-file card (Demand Master or Last Month PAR).
 * Upload saves the file only (fast); Save to DB performs DuckDB ingestion.
 *
 * Props:
 *   file     — entry from summarizeDb().files (saved/displayName/size/modified/loaded/rowCount)
 *   onChange — called after a successful upload/ingest so the page refreshes status
 */
export default function MasterFileCard({ file, onChange }) {
  const toast = useToast();
  const [picked, setPicked] = useState(null);
  const [busy, setBusy] = useState("");

  const uploadedWhen = formatWhen(file.modified);

  async function handleUpload(f) {
    if (!f) return;
    setPicked(f);
    setBusy("upload");
    try {
      await uploadMasterFile(file.backendKey, f);
      toast.success(`${file.label} uploaded. Click Save to DB to load it.`, "Uploaded");
      setPicked(null);
      await onChange?.();
    } catch (e) {
      toast.error(e.message, "Upload failed");
    } finally {
      setBusy("");
    }
  }

  async function handleSaveToDb() {
    setBusy("ingest");
    try {
      const res = await ingestMasterFile(file.ingestKey);
      if (res?.success === false) {
        toast.error(res.message || "Could not load into DuckDB.", "Save to DB failed");
      } else {
        toast.success(res?.message || `${file.label} loaded into DuckDB.`, "Saved to DB");
      }
      await onChange?.();
    } catch (e) {
      toast.error(e.message, "Save to DB failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="panel db-card">
      <div className="db-card-head">
        <div className="db-card-title">
          <Database size={17} className="muted" />
          <h3>{file.label}</h3>
        </div>
        <div className="db-badges">
          <span className={`badge ${file.saved ? "badge-success" : "badge-muted"}`}>
            {file.saved ? "Available" : "Missing"}
          </span>
          <span className={`badge ${file.loaded ? "badge-info" : "badge-muted"}`}>
            {file.loaded ? "DuckDB Loaded" : "Not loaded"}
          </span>
        </div>
      </div>

      <div className="db-card-meta">
        {file.saved ? (
          <div className="file-pill" title={file.displayName || file.savedName}>
            <FileSpreadsheet size={14} />
            <span className="db-card-filename">{file.displayName || file.savedName}</span>
          </div>
        ) : (
          <p className="db-card-empty">No file saved yet.</p>
        )}

        <dl className="db-card-facts">
          <div>
            <dt>Uploaded</dt>
            <dd>{uploadedWhen || "—"}</dd>
          </div>
          <div>
            <dt>Size</dt>
            <dd>{file.size != null ? fileSizeMB(file.size) : "—"}</dd>
          </div>
          <div>
            <dt>Rows in DuckDB</dt>
            <dd>{file.loaded ? (file.rowCount?.toLocaleString?.() ?? file.rowCount) : "—"}</dd>
          </div>
        </dl>
      </div>

      <FileDrop
        label={file.saved ? `Replace ${file.label}` : `Upload ${file.label}`}
        hint="Excel file (.xlsx)"
        file={picked}
        onFile={handleUpload}
        disabled={Boolean(busy)}
      />

      <div className="actions db-card-actions">
        <Button
          variant="primary"
          icon={UploadCloud}
          className="grow"
          disabled={!file.saved || Boolean(busy)}
          loading={busy === "ingest"}
          onClick={handleSaveToDb}
        >
          Save to DB
        </Button>
      </div>
    </div>
  );
}
