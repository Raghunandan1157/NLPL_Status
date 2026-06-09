import EmbeddedModule from "../embedded/EmbeddedModule.jsx";

export default function AnalyticsModule() {
  return (
    <EmbeddedModule
      path="/analytics/"
      eyebrow="Analytics Module"
      title="Portfolio Analytics"
      subtitle="Trends, DPD flow, rankings, heatmaps and projections — migrated from unified-collection-report."
    />
  );
}
