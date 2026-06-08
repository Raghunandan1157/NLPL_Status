import { useState } from "react";
import { Send } from "lucide-react";
import { Button, useToast } from "../components/ui.jsx";
import { isValidEmail } from "../lib/format.js";
import MailConnect from "../shared/MailConnect.jsx";
import { sendEmail } from "./disbApi.js";

export default function DisbEmail({ onHealthChange }) {
  const toast = useToast();
  const [raw, setRaw] = useState("");
  const [subject, setSubject] = useState("Disbursement Report");
  const [busy, setBusy] = useState(false);

  async function handleSend() {
    const emails = raw
      .split(/[\n,;]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const invalid = emails.filter((e) => !isValidEmail(e));
    if (emails.length === 0) {
      toast.warn("Add at least one recipient email.");
      return;
    }
    if (invalid.length) {
      toast.error(`Invalid email(s): ${invalid.join(", ")}`, "Check recipients");
      return;
    }
    setBusy(true);
    try {
      const res = await sendEmail(emails.map((email) => ({ email })), subject);
      if (res.success) toast.success(res.message || "Email sent.", "Sent");
      else toast.error(res.message || "Send failed.", "Email");
    } catch (e) {
      toast.error(e.message, "Send failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="eod-grid">
      <div className="col" style={{ gap: 18 }}>
        <div className="panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Deliver</p>
              <h2>Email the Disbursement Report</h2>
              <p className="sub">Sends the latest generated report. Gmail login is shared across all modules.</p>
            </div>
          </div>

          <MailConnect onHealthChange={onHealthChange} />

          <label className="field" style={{ marginTop: 16 }}>
            <span>Subject</span>
            <input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} />
          </label>

          <label className="field" style={{ marginTop: 12 }}>
            <span>Recipients (one per line, or comma-separated)</span>
            <textarea
              className="input"
              style={{ minHeight: 120, resize: "vertical" }}
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              placeholder={"team@example.com\nmanager@example.com"}
            />
          </label>

          <Button
            variant="success"
            icon={Send}
            className="btn-block"
            style={{ marginTop: 14 }}
            loading={busy}
            onClick={handleSend}
          >
            Send Email
          </Button>
        </div>
      </div>

      <div className="col" style={{ gap: 18 }}>
        <div className="panel hint-panel">
          <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
            The report attached is the most recently processed Disbursement report
            (<b>DB_Disbursement_Report.xlsx</b>). Run a process first if you haven't yet. Connect
            Gmail once here and the same login is used by every module that sends mail.
          </p>
        </div>
      </div>
    </div>
  );
}
