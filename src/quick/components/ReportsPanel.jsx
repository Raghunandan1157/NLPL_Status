import ReportHistory from "../../components/ReportHistory.jsx";
import { reportArchiveFileUrl, reportArchiveList } from "../quickApi.js";

const FALLBACK = [{ type: "output", label: "Quick Hourly Report", name: "" }];

export default function ReportsPanel() {
  return (
    <ReportHistory
      eyebrow="Quick Report"
      title="Reports & Downloads"
      subtitle="Every generated Quick report, grouped by date. Previous runs stay downloadable for 3 days."
      listFn={reportArchiveList}
      fileUrlFn={reportArchiveFileUrl}
      emptyHint="Generate a Quick report to see it here."
      fallbackReports={FALLBACK}
    />
  );
}
