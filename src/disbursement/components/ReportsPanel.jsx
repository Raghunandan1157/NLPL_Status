import ReportsDownloadSection from "../../shared/processing/ReportsDownloadSection.jsx";
import { reportArchiveFileUrl, reportArchiveList } from "../disbApi.js";

export default function ReportsPanel() {
  return (
    <ReportsDownloadSection
      listFn={reportArchiveList}
      fileUrlFn={reportArchiveFileUrl}
      moduleLabel="Disbursement"
      eyebrow="Disbursement Report"
      title="Reports & Downloads"
      subtitle="Pick a date to see the Disbursement reports generated that day. Previous runs stay downloadable for 3 days."
      emptyHint="Process a disbursement file to see it here."
    />
  );
}
