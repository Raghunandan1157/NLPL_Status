import { AlertTriangle, BarChart3, CalendarDays, CalendarRange, CloudUpload, Database, LineChart, Users, Wallet, Zap } from "lucide-react";
import EodModule from "../eod/EodModule.jsx";
import HourlyModule from "../hourly/HourlyModule.jsx";
import QuickModule from "../quick/QuickModule.jsx";
import QmeModule from "../qme/QmeModule.jsx";
import OndateModule from "../ondate/OndateModule.jsx";
import OdReportModule from "../od_report/OdReportModule.jsx";
import DisbursementModule from "../disbursement/DisbursementModule.jsx";
import InstantModule from "../instant/InstantModule.jsx";
import DisbEc2Module from "../disbursement_ec2/DisbEc2Module.jsx";
import SupabaseModule from "../supabase/SupabaseModule.jsx";

/**
 * Central module registry. The home page and sidebar are generated from this
 * list, so adding a new module later is just one entry here + its component.
 *
 *   status: "live" → openable (has a Component)
 *           "soon" → shown on the home grid as a roadmap card (not openable)
 */
export const MODULES = [
  {
    id: "eod",
    name: "EOD Module",
    tagline: "Regular Demand vs Collection",
    description:
      "Process the daily PAR, Collection & Demand files, generate the EOD report, then email and WhatsApp it to your teams.",
    icon: BarChart3,
    accent: "indigo",
    status: "live",
    Component: EodModule,
    features: ["File processing", "Report generation", "Email dispatch", "WhatsApp send"],
  },
  {
    id: "hourly",
    name: "Hourly Module",
    tagline: "Hourly Demand vs Collection",
    description:
      "Merge live collection data onto today's EOD Output, download VBA scripts, run automation, and dispatch files.",
    icon: BarChart3,
    accent: "violet",
    status: "live",
    Component: HourlyModule,
    features: ["File merging", "VBA templates", "Excel automation", "WhatsApp send"],
  },
  {
    id: "quick",
    name: "Quick Report",
    tagline: "One-shot hourly report",
    description:
      "Generate the hourly fast report from PAR, Collection and the hourly Collection Report in a single pass, then sync to the dashboard.",
    icon: Zap,
    accent: "amber",
    status: "live",
    Component: QuickModule,
    features: ["3-file processing", "Hourly fast report", "Dashboard sync", "Report history"],
  },
  {
    id: "quick_month_end",
    name: "Month-End Report",
    tagline: "Month-end employee report",
    description:
      "Generate the month-end Employee report from Demand, Last Month PAR, PAR and Collection — with EOD output, EOD report and portfolio sync.",
    icon: CalendarRange,
    accent: "emerald",
    status: "live",
    Component: QmeModule,
    features: ["4-file processing", "Month-end rules", "Column validation", "Report history"],
  },
  {
    id: "ondate",
    name: "On-Date Report",
    tagline: "Monthly on-date extraction",
    description:
      "Extract per-date On-Date sheets into a monthly master workbook with full formatting preserved, then download the month report.",
    icon: CalendarDays,
    accent: "violet",
    status: "live",
    Component: OndateModule,
    features: ["Per-date extraction", "Monthly master report", "Formatting preserved", "Report downloads"],
  },
  {
    id: "od_report",
    name: "OD Report",
    tagline: "Overdue report",
    description:
      "Generate the Overdue (OD) report from PAR with FTOD and Insurance-OD analysis against month-end and insurance files.",
    icon: AlertTriangle,
    accent: "amber",
    status: "live",
    Component: OdReportModule,
    features: ["FTOD analysis", "Insurance-OD matching", "Live step progress", "Saved to Downloads"],
  },
  {
    id: "instant",
    name: "Instant Report",
    tagline: "Instant pivot summaries",
    description:
      "Generate instant pivot summaries (Regular, DPD buckets, NPA) by Region / Area / Branch from PAR + Collection, cached per date.",
    icon: LineChart,
    accent: "violet",
    status: "live",
    Component: InstantModule,
    features: ["Pivot summaries", "Per-date history", "Monthly backend data", "Excel export"],
  },
  {
    id: "disbursement_ec2",
    name: "Disbursement Sync",
    tagline: "Push to EC2 database",
    description:
      "Aggregate an ESAF disbursement export by date / branch / officer / product and push it to the Coll_Db EC2 Postgres database.",
    icon: CloudUpload,
    accent: "amber",
    status: "live",
    Component: DisbEc2Module,
    features: ["CSV/XLSX parsing", "Per-date preview", "Override-by-date", "EC2 Postgres push"],
  },
  {
    id: "supabase_sync",
    name: "Supabase Sync",
    tagline: "Grow_With_Me staging",
    description:
      "Mirror EOD daily, Quick hourly and disbursement data into the Supabase Grow_With_Me staging tables.",
    icon: Database,
    accent: "emerald",
    status: "live",
    Component: SupabaseModule,
    features: ["Daily sync", "Hourly sync", "Disbursement sync", "Override semantics"],
  },
  {
    id: "employee",
    name: "Employee Performance",
    tagline: "Officer-level dashboards",
    description: "Field-officer collection performance dashboards and exports.",
    icon: Users,
    accent: "emerald",
    status: "soon",
  },
  {
    id: "disbursement_report",
    name: "Disbursement Report",
    tagline: "Daily disbursement",
    description:
      "Enrich the disbursement export with Product Name, Region/Area (via BranchID) and Employee ID, then email the report or run VBA.",
    icon: Wallet,
    accent: "amber",
    status: "live",
    Component: DisbursementModule,
    features: ["Browser processing", "VLOOKUP enrichment", "Centralized email", "Report history"],
  },
  {
    id: "analytics",
    name: "Analytics",
    tagline: "Portfolio insights",
    description: "Trends, cohorts and portfolio analytics across modules.",
    icon: LineChart,
    accent: "violet",
    status: "soon",
  },
];

export const liveModules = () => MODULES.filter((m) => m.status === "live");
export const getModule = (id) => MODULES.find((m) => m.id === id);
