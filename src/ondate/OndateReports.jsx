import { useCallback, useEffect, useState } from "react";
import { CalendarDays, Download, FileSpreadsheet, RefreshCw } from "lucide-react";
import { Button, Spinner } from "../components/ui.jsx";
import { downloadReportUrl, listReports } from "./ondateApi.js";

export default function OndateReports({ reloadKey }) {
  const [reports, setReports] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listReports();
      setReports(res.reports || []);
    } catch {
      setReports([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">On-Date Report</p>
          <h2>Reports & Downloads</h2>
          <p className="sub">One master workbook per month. Newest first.</p>
        </div>
        <Button size="sm" variant="ghost" icon={RefreshCw} onClick={load}>
          Refresh
        </Button>
      </div>

      {loading ? (
        <div className="row" style={{ gap: 10, padding: 20 }}>
          <Spinner size={18} /> Loading reports…
        </div>
      ) : !reports || reports.length === 0 ? (
        <div className="empty">
          <FileSpreadsheet size={28} />
          <h3>No reports yet</h3>
          <p className="muted">Extract an On-Date report to see it here.</p>
        </div>
      ) : (
        <div className="report-cards">
          {reports.map((r) => (
            <div key={r.path} className="report-card">
              <span className="report-ic violet">
                <CalendarDays size={20} />
              </span>
              <div className="grow">
                <strong>{r.folder}</strong>
                <p className="muted">
                  {r.filename}
                  {r.created ? ` · ${new Date(r.created).toLocaleString()}` : ""}
                </p>
              </div>
              <a className="btn btn-primary btn-sm" href={downloadReportUrl(r.path)}>
                <Download size={15} /> Download
              </a>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
