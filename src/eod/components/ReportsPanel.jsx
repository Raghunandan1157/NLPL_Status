import ReportHistory from "../../components/ReportHistory.jsx";
import { reportArchiveFileUrl, reportArchiveList } from "../api.js";

export default function ReportsPanel() {
  return (
    <ReportHistory
      eyebrow="Reports & Downloads"
      title="Download by date & run"
      subtitle="Every EOD run is archived. All runs from the last 3 days are downloadable; older runs are auto-deleted."
      listFn={reportArchiveList}
      fileUrlFn={reportArchiveFileUrl}
      emptyHint="Run EOD processing on the Process tab — both reports are saved here per run."
    />
  );
}
