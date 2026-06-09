import ReportsDownloadSection from "../../shared/processing/ReportsDownloadSection.jsx";
import { reportArchiveFileUrl, reportArchiveList } from "../hourlyApi.js";

export default function ReportsPanel() {
  return (
    <ReportsDownloadSection
      listFn={reportArchiveList}
      fileUrlFn={reportArchiveFileUrl}
      moduleLabel="Hourly"
      eyebrow="Hourly Reports & Downloads"
      title="Download by date & run"
      subtitle="Every Hourly run is archived newest-first. Reports from the last 3 days are downloadable; older runs auto-delete."
      emptyHint="Run Hourly processing on the Process tab — generated reports are saved here per run."
    />
  );
}
