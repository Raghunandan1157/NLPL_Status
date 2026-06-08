import ReportHistory from "../../components/ReportHistory.jsx";
import { reportArchiveFileUrl, reportArchiveList } from "../disbApi.js";

const FALLBACK = [{ type: "output", label: "Disbursement Report", name: "" }];

export default function ReportsPanel() {
  return (
    <ReportHistory
      eyebrow="Disbursement Report"
      title="Reports & Downloads"
      subtitle="Every generated Disbursement report, grouped by date. Previous runs stay downloadable for 3 days."
      listFn={reportArchiveList}
      fileUrlFn={reportArchiveFileUrl}
      emptyHint="Process a disbursement file to see it here."
      fallbackReports={FALLBACK}
    />
  );
}
