import ReportsDownloadSection from "../../shared/processing/ReportsDownloadSection.jsx";
import { reportArchiveFileUrl, reportArchiveList } from "../api.js";

export default function ReportsPanel() {
  return (
    <ReportsDownloadSection
      listFn={reportArchiveList}
      fileUrlFn={reportArchiveFileUrl}
      moduleLabel="EOD"
      eyebrow="Reports & Downloads"
      title="Download by date & run"
      subtitle="Every EOD run is archived newest-first. Reports from the last 3 days are downloadable; older runs auto-delete."
      emptyHint="Run EOD processing on the Process tab — both reports are saved here per run."
    />
  );
}
