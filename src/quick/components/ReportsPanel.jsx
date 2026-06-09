import ReportsDownloadSection from "../../shared/processing/ReportsDownloadSection.jsx";
import { reportArchiveFileUrl, reportArchiveList } from "../quickApi.js";

export default function ReportsPanel() {
  return (
    <ReportsDownloadSection
      listFn={reportArchiveList}
      fileUrlFn={reportArchiveFileUrl}
      moduleLabel="Quick"
      eyebrow="Quick Report"
      title="Reports & Downloads"
      subtitle="Pick a date to see the Quick reports generated that day. Previous runs stay downloadable for 3 days."
      emptyHint="Generate a Quick report to see it here."
    />
  );
}
