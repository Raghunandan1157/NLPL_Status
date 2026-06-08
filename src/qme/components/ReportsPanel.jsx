import ReportHistory from "../../components/ReportHistory.jsx";
import { reportArchiveFileUrl, reportArchiveList } from "../qmeApi.js";

const FALLBACK = [
  { type: "output", label: "Regular Demand vs Collection", name: "" },
  { type: "report", label: "EOD Report", name: "" },
  { type: "employee", label: "Month-End Employee Report", name: "" },
];

export default function ReportsPanel() {
  return (
    <ReportHistory
      eyebrow="Month-End Report"
      title="Reports & Downloads"
      subtitle="Every month-end run, grouped by date. Previous runs stay downloadable for 3 days."
      listFn={reportArchiveList}
      fileUrlFn={reportArchiveFileUrl}
      emptyHint="Generate a month-end report to see it here."
      fallbackReports={FALLBACK}
    />
  );
}
