import ReportsDownloadSection from "../../shared/processing/ReportsDownloadSection.jsx";
import { reportArchiveFileUrl, reportArchiveList } from "../qmeApi.js";

export default function ReportsPanel() {
  return (
    <ReportsDownloadSection
      listFn={reportArchiveList}
      fileUrlFn={reportArchiveFileUrl}
      moduleLabel="Month-End"
      eyebrow="Month-End Report"
      title="Reports & Downloads"
      subtitle="Pick a date to see the month-end reports generated that day. Previous runs stay downloadable for 3 days."
      emptyHint="Generate a month-end report to see it here."
    />
  );
}
