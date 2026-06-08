import { useMemo, useState } from "react";
import { CloudUpload, Database, Eye, Sparkles } from "lucide-react";
import { Button, FileDrop, Spinner, useToast } from "../components/ui.jsx";
import { preview as previewApi, process as processApi } from "./disbEc2Api.js";
import "../eod/eod.css";
import "../instant/instant.css"; // .data-table styles

const inr = (n) => "₹" + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 });

export default function DisbEc2Module() {
  const toast = useToast();
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState("");
  const [data, setData] = useState(null); // {dates, csv_rows, cancelled_total}
  const [selected, setSelected] = useState(() => new Set());

  const allSelected = data && selected.size === data.dates.length && data.dates.length > 0;

  function toggle(date) {
    setSelected((s) => {
      const n = new Set(s);
      n.has(date) ? n.delete(date) : n.add(date);
      return n;
    });
  }

  async function handlePreview() {
    if (!file || busy) return;
    setBusy("preview");
    setData(null);
    try {
      const res = await previewApi(file);
      if (!res.success) {
        toast.error(res.message || "Preview failed.");
        return;
      }
      setData(res);
      setSelected(new Set(res.dates.map((d) => d.date)));
      toast.success(`${res.csv_rows} rows · ${res.dates.length} date(s).`, "Previewed");
    } catch (e) {
      toast.error(e.message, "Preview failed");
    } finally {
      setBusy("");
    }
  }

  async function handleProcess() {
    if (!file || selected.size === 0 || busy) return;
    setBusy("process");
    try {
      const res = await processApi(file, [...selected]);
      if (res.success) toast.success(res.message || "Pushed to EC2.", "Synced");
      else toast.error(res.message || "Push failed.", "EC2");
    } catch (e) {
      toast.error(e.message, "Push failed");
    } finally {
      setBusy("");
    }
  }

  const totals = useMemo(() => {
    if (!data) return null;
    const sel = data.dates.filter((d) => selected.has(d.date));
    return {
      rows: sel.reduce((a, d) => a + d.row_count, 0),
      amount: sel.reduce((a, d) => a + d.amount, 0),
    };
  }, [data, selected]);

  return (
    <div className="eod">
      <div className="eod-head">
        <div>
          <p className="eyebrow">Disbursement Sync</p>
          <h1 className="eod-title">Disbursement → EC2 Database</h1>
          <p className="muted eod-subtitle">
            Aggregate an ESAF disbursement export by date / branch / officer / product and push to Coll_Db.
          </p>
        </div>
      </div>

      <div className="eod-grid">
        <div className="col" style={{ gap: 18 }}>
          <div className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Step 1 · Source</p>
                <h2>Upload & Preview</h2>
                <p className="sub">ESAF ClientDisbursementDetail export (.csv / .xlsx).</p>
              </div>
              {busy && (
                <span className="badge badge-info">
                  <Spinner size={13} /> {busy === "process" ? "Pushing" : "Reading"}
                </span>
              )}
            </div>

            <div className="file-grid" style={{ gridTemplateColumns: "1fr" }}>
              <FileDrop
                label="Disbursement export"
                hint="Required · .csv / .xlsx"
                accept=".csv,.xlsx,.xls"
                file={file}
                onFile={(f) => {
                  setFile(f);
                  setData(null);
                }}
                disabled={Boolean(busy)}
              />
            </div>

            <div className="actions">
              <Button variant="primary" icon={Eye} className="grow" disabled={!file || Boolean(busy)} loading={busy === "preview"} onClick={handlePreview}>
                Preview dates
              </Button>
            </div>
          </div>

          {data && (
            <div className="panel">
              <div className="panel-header" style={{ marginBottom: 12 }}>
                <div>
                  <p className="eyebrow">Step 2 · Select dates</p>
                  <h2>Per-date summary</h2>
                  <p className="sub">
                    {data.csv_rows} rows · {data.cancelled_total} cancelled (excluded). Pick the dates to push.
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setSelected(allSelected ? new Set() : new Set(data.dates.map((d) => d.date)))}
                >
                  {allSelected ? "Clear" : "Select all"}
                </Button>
              </div>

              <div style={{ overflowX: "auto" }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th></th>
                      <th>Date</th>
                      <th style={{ textAlign: "right" }}>Active rows</th>
                      <th style={{ textAlign: "right" }}>Amount</th>
                      <th style={{ textAlign: "right" }}>Cancelled</th>
                      <th>In DB</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.dates.map((d) => (
                      <tr key={d.date}>
                        <td>
                          <input type="checkbox" checked={selected.has(d.date)} onChange={() => toggle(d.date)} />
                        </td>
                        <td>{d.date}</td>
                        <td style={{ textAlign: "right" }}>{d.row_count}</td>
                        <td style={{ textAlign: "right" }}>{inr(d.amount)}</td>
                        <td style={{ textAlign: "right" }}>
                          {d.cancelled_count ? `${d.cancelled_count} · ${inr(d.cancelled_amount)}` : "—"}
                        </td>
                        <td>
                          {d.exists ? (
                            <span className="badge badge-warn">{d.exists.count} rows (overwrite)</span>
                          ) : (
                            <span className="badge badge-muted">new</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="actions" style={{ marginTop: 16 }}>
                <Button
                  variant="success"
                  icon={CloudUpload}
                  className="grow"
                  disabled={selected.size === 0 || Boolean(busy)}
                  loading={busy === "process"}
                  onClick={handleProcess}
                >
                  Push {selected.size} date{selected.size === 1 ? "" : "s"} to EC2
                  {totals ? ` · ${totals.rows} rows · ${inr(totals.amount)}` : ""}
                </Button>
              </div>
            </div>
          )}
        </div>

        <div className="col" style={{ gap: 18 }}>
          <div className="panel hint-panel">
            <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
              <Sparkles size={18} className="muted" />
              <div>
                <strong style={{ fontSize: 13.5 }}>How Disbursement Sync works</strong>
                <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                  Repairs the CSV, keeps <b>Active</b> rows, aggregates by date / branch / officer /
                  product, and pushes to the Coll_Db <b>disbursement_daily</b> table (overwriting the
                  selected dates).
                </p>
                <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
                  <Database size={11} /> Requires the EC2 SSH key + database to be reachable. Preview
                  works offline; the push needs that infrastructure.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
