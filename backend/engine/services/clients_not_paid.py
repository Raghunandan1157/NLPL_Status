"""Clients Not Paid — additional Hourly output file.

Independent, additive step of the Hourly run. It does NOT touch the Hourly
Report calculations: it only *reads* data the hourly pipeline has already
loaded and writes one extra workbook.

    Latest EOD output ( = the "Regular Demand vs Collection" workbook )
        -> FTOD rows      — demand up to the EOD date the EOD reports as not paid
        +  TODAY's demand — the days after the EOD, not collected in the EOD file
        -> drop every account that paid in the uploaded Hourly Collection Report
        = Clients Not Paid, carrying every column of the source workbook

The population therefore spans BOTH the carry-forward from the last EOD and the
demand that has fallen due since it, e.g. 7,699 FTOD at yesterday's EOD plus
10,992 due today = 18,691 to collect, minus everyone who has paid in today's
hourly collection file (yesterday's 100 + today's ~9,000) = the clients listed.

FTOD is the report's shortfall column, NOT the demand base. In the OverAll
sheet's REGULAR DEMAND VS COLLECTION block the columns are

    DEMAND | COLLECTION | FTOD | COLLECTION %

and daily_report_builder writes ``FTOD = reg_demand - reg_collection``, which
resolves (see eod_processor._compute_precomputed_sheets) to

    reg_demand     = sum("No of Regular Demand") where Meeting Date is in
                     first-of-month .. report date              ("the demand")
    reg_collection = the same sum EXCLUDING DPD Group "1-30"    ("paid")
    FTOD           = the difference = the rows still sitting in DPD Group
                     "1-30" — this month's demand that has not been paid.

So this module's population is exactly those FTOD rows (e.g. the 07-08-2026
report's 77,041 demand - 67,338 collection = 9,703 FTOD accounts), and the
output is the ones among them that have still not paid in the hourly file.

Verification identity (printed on the Summary sheet, tying to the report):

    Regular Demand up to the EOD date - collected at EOD = FTOD   (report's row)
    FTOD + demand due after the EOD and not collected     = To collect
    To collect - paid in the Hourly Collection            = Clients Not Paid

The two segments are split on the EOD's OWN date (see _resolve_eod_date), and
each is judged by the rule that applies to it:

  * up to the EOD date  — the EOD has assessed these, so the report's own
    shortfall rule decides: still sitting in DPD Group "1-30" = not paid. This
    segment alone reproduces the report's FTOD column (the 07-08-2026 report:
    77,041 demand - 67,338 collection = 9,703 FTOD).
  * after the EOD date, through the hourly run's date — no EOD has assessed
    these yet, so the DPD bucket cannot be used; the workbook's own "Collection"
    value decides: nothing collected = not paid.

Rows in the output are tagged in the ``Unpaid Segment`` column with which of the
two they came from — "FTOD up to <EOD date>" and "Demand on <today's date>" —
and the Summary sheet states both counts separately.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from services.column_matcher import find_column
from services.eod_processor import parse_date_column

logger = logging.getLogger(__name__)

OUTPUT_BASENAME = 'Clients_Not_Paid.xlsx'
LATEST_FILENAME = 'Clients_Not_Paid_Latest.xlsx'

DATA_SHEET = 'Clients Not Paid'
SUMMARY_SHEET = 'Summary'

# Tag column added to the output so each listed client shows which segment it
# came from: the carry-forward FTOD, or demand that fell due after the EOD.
SEGMENT_COL = 'Unpaid Segment'


def _eod_collection_column(eod_df):
    """The EOD workbook's own collection amount column.

    Matched strictly: the hourly pipeline appends a "Collection as on <date>"
    working column and the workbook carries "Collection Date", neither of which
    is the collected amount — a loose match would silently pick one of them.
    """
    for col in eod_df.columns:
        if str(col).strip().lower() == 'collection':
            return col
    return None


def normalise_account_key(series: pd.Series) -> pd.Series:
    """Account key normaliser — identical rules to the hourly merge key so an
    account matches whether it was read as int, float or text."""
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r'\.0$', '', regex=True)
    )


def _resolve_anchor(meeting_dt: pd.Series, report_date) -> pd.Timestamp:
    """Report date to measure FTOD against, auto-corrected when the workbook
    holds a different month than the requested date (same safety net as
    eod_processor._compute_precomputed_sheets, which would otherwise report
    zero demand)."""
    anchor = pd.Timestamp(report_date).normalize()
    in_window = (meeting_dt >= anchor.replace(day=1)) & (meeting_dt <= anchor)
    if in_window.any() or not meeting_dt.notna().any():
        return anchor

    month_counts = meeting_dt.dropna().dt.to_period('M').value_counts()
    if month_counts.empty:
        return anchor
    best = month_counts.idxmax()
    last_day = best.days_in_month
    corrected = pd.Timestamp(year=best.year, month=best.month,
                             day=min(anchor.day, last_day))
    logger.warning(
        "CLIENTS NOT PAID: report date %s matched 0 Meeting Date rows — "
        "auto-correcting to %s (month with most data: %d rows)",
        anchor.date(), corrected.date(), int(month_counts.iloc[0]),
    )
    return corrected


def _resolve_eod_date(eod_df, first_of_month, report_date, declared=None):
    """The date the EOD output itself was built for.

    The FTOD population must be measured against the EOD's own date, not the
    hourly run's date — the spec is "the FTOD data from the most recent EOD
    report". Running the hourly at 08-08 on a 07-08 EOD must still reproduce
    the 07-08 report's DEMAND / COLLECTION / FTOD row; extending the window to
    08-08 would add that day's demand, which the EOD never measured.

    Taken from the workbook itself: the latest "Collection Date", which the EOD
    run stamps as it applies collections. That is preferred over the caller's
    declared date (the backend ``.target_date`` marker) because the marker is
    shared with the other modules that write it and goes stale the moment an
    older EOD workbook is used — and a marker one day ahead of the data would
    pull in a day of demand no EOD has assessed. The marker is the fallback for
    a workbook with no usable Collection Date; the hourly report date is the
    last resort. Any candidate outside the report's own month window is
    ignored.
    """
    def _valid(value):
        try:
            ts = pd.Timestamp(value).normalize()
        except (ValueError, TypeError):
            return None
        return ts if first_of_month <= ts <= report_date else None

    coll_date_col = find_column(eod_df, 'Collection Date', 'CollectionDate')
    if coll_date_col:
        derived = parse_date_column(eod_df[coll_date_col]).max()
        if pd.notna(derived):
            resolved = _valid(derived)
            if resolved is not None:
                return resolved

    if declared is not None:
        resolved = _valid(declared)
        if resolved is not None:
            return resolved

    return report_date


def _write_workbook(df: pd.DataFrame, summary_rows: list, output_path) -> None:
    """Write the workbook: a Summary sheet (counts, verifiable) followed by one
    row per unpaid client carrying the source columns verbatim."""
    import xlsxwriter

    wb = xlsxwriter.Workbook(str(output_path), {
        'constant_memory': True,      # stream rows; bounded memory on big months
        'default_date_format': 'dd-mm-yyyy',
    })
    try:
        f_title = wb.add_format({'bold': True, 'font_size': 13})
        f_label = wb.add_format({'bold': True})
        f_num = wb.add_format({'num_format': '#,##0'})
        f_hdr = wb.add_format({
            'bold': True, 'bg_color': '#1F4E78', 'font_color': '#FFFFFF',
            'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True,
        })

        # ── Summary (written first: constant_memory finalises each sheet in turn)
        ws = wb.add_worksheet(SUMMARY_SHEET)
        ws.set_column(0, 0, 46)
        ws.set_column(1, 1, 22)
        ws.write(0, 0, 'Clients Not Paid — summary', f_title)
        for i, (label, value) in enumerate(summary_rows, start=2):
            ws.write(i, 0, label, f_label)
            if isinstance(value, (int, np.integer)):
                ws.write_number(i, 1, int(value), f_num)
            else:
                ws.write(i, 1, '' if value is None else str(value))

        # ── Data
        ws = wb.add_worksheet(DATA_SHEET)
        cols = list(df.columns)
        ws.set_row(0, 30)
        for c, name in enumerate(cols):
            ws.write(0, c, str(name), f_hdr)
            ws.set_column(c, c, min(max(len(str(name)) + 2, 10), 32))
        ws.freeze_panes(1, 0)
        if len(df):
            ws.autofilter(0, 0, len(df), max(len(cols) - 1, 0))

        # Values are written as-is: numbers stay numbers, text stays text,
        # blanks stay blank. Nothing is defaulted or invented.
        for r, row in enumerate(df.itertuples(index=False, name=None), start=1):
            for c, value in enumerate(row):
                if value is None or (isinstance(value, float) and np.isnan(value)) \
                        or value is pd.NaT:
                    continue
                if isinstance(value, (pd.Timestamp,)):
                    ws.write_datetime(r, c, value.to_pydatetime())
                elif isinstance(value, (int, float, np.integer, np.floating)):
                    ws.write_number(r, c, float(value))
                elif isinstance(value, (bool, np.bool_)):
                    ws.write_boolean(r, c, bool(value))
                else:
                    ws.write_string(r, c, str(value))
    finally:
        wb.close()


def build_clients_not_paid(eod_df, *, account_col, paid_keys, report_date,
                           output_path, source_columns=None, report_time='',
                           collection_rows=0, collection_accounts=0,
                           eod_report_date=None, eod_collection_col='auto'):
    """Build ``Clients_Not_Paid.xlsx`` from the EOD output already in memory.

    Args:
        eod_df: the EOD output / "Regular Demand vs Collection" DataFrame. Read
            only — never mutated.
        account_col: the account-id column of ``eod_df`` (the unique identifier;
            names are never used for matching).
        paid_keys: set of NORMALISED account keys that paid in the uploaded
            Hourly Collection Report (already de-duplicated: an account that
            appears many times counts once).
        report_date: the date the hourly report is generated for.
        output_path: destination .xlsx.
        source_columns: columns to carry into the output — pass the EOD
            workbook's ORIGINAL columns so hourly-only working columns
            (Remark / Remark2 / "Collection as on …") are left out.
        report_time / collection_rows / collection_accounts: recorded on the
            Summary sheet for traceability.
        eod_collection_col: the column holding the EOD's OWN collected amount,
            used to decide whether demand due after the EOD date was already
            collected. ``'auto'`` finds it; pass ``None`` when the frame's
            'Collection' column has been overwritten with hourly values (the
            fast-report path), so that demand is judged by the hourly file alone.

    Returns a stats dict, or None if the file could not be produced (caller
    treats that as non-fatal — the Hourly Report is unaffected either way).
    """
    output_path = Path(output_path)

    meeting_col = find_column(eod_df, 'Meeting Date', 'MeetingDate')
    if not meeting_col:
        logger.warning("CLIENTS NOT PAID: no 'Meeting Date' column — skipped")
        return None
    if account_col not in eod_df.columns:
        logger.warning("CLIENTS NOT PAID: account column %r missing — skipped", account_col)
        return None

    meeting_dt = parse_date_column(eod_df[meeting_col]).dt.normalize()
    anchor = _resolve_anchor(meeting_dt, report_date)
    first_of_month = anchor.replace(day=1)

    # The EOD's own date splits the two segments. Everything up to it has been
    # assessed by an EOD; everything after it (through the hourly run's date)
    # has not, and is judged on the workbook's own Collection value instead.
    eod_date = _resolve_eod_date(eod_df, first_of_month, anchor, eod_report_date)
    ftod_mask = (meeting_dt >= first_of_month) & (meeting_dt <= eod_date)
    today_mask = (meeting_dt > eod_date) & (meeting_dt <= anchor)

    # The report's demand base: a row inside the window that carries a regular
    # demand instalment (the report's DEMAND column).
    no_reg_col = find_column(eod_df, 'No of Regular Demand', 'No Of Regular Demand')
    if no_reg_col:
        no_reg = pd.to_numeric(eod_df[no_reg_col], errors='coerce').fillna(0)
        has_demand = no_reg > 0
    else:
        logger.warning(
            "CLIENTS NOT PAID: no 'No of Regular Demand' column — using every "
            "row inside the FTOD window as the demand base"
        )
        has_demand = pd.Series(True, index=eod_df.index)
    demand_mask = ftod_mask & has_demand
    today_demand_mask = today_mask & has_demand

    # "Nothing collected in the EOD file" — the rule for the days no EOD has
    # assessed yet, and the fallback for the assessed days when the workbook
    # carries no DPD bucket.
    coll_col = (_eod_collection_column(eod_df) if eod_collection_col == 'auto'
                else eod_collection_col)
    if coll_col and coll_col in eod_df.columns:
        eod_collection = pd.to_numeric(eod_df[coll_col], errors='coerce').fillna(0)
        nothing_collected = eod_collection <= 0
    else:
        logger.warning("CLIENTS NOT PAID: no 'Collection' column — demand after "
                       "the EOD date is treated as wholly uncollected")
        nothing_collected = pd.Series(True, index=eod_df.index)

    # FTOD = the demand rows the report counts as NOT collected, i.e. the ones
    # still in DPD Group "1-30" (reg_demand - reg_collection). This reproduces
    # the number printed in the report's FTOD column.
    dpd_col = find_column(eod_df, 'DPD Group')
    if dpd_col:
        unpaid_at_eod = (
            eod_df[dpd_col].astype(str).str.contains('1-30', case=False, na=False)
        )
    else:
        # No DPD bucket to read: fall back to "nothing collected", same intent
        # without the report's bucket rule.
        logger.warning("CLIENTS NOT PAID: no 'DPD Group' column — falling back "
                       "to rows with no recorded collection")
        unpaid_at_eod = nothing_collected
    ftod_rows = demand_mask & unpaid_at_eod

    # Demand that fell due after the EOD (normally today's). The DPD bucket is
    # stale for these rows — it was computed before they were due — so only the
    # workbook's recorded collection can say whether anything came in.
    today_rows = today_demand_mask & nothing_collected

    # Both segments together are what is still to be collected right now.
    to_collect_rows = ftod_rows | today_rows

    # Accounts already unpaid whose meeting date falls LATER this month. They
    # are outside the report-date window so they are not listed, but the Hourly
    # Report's own DEMAND column includes them (it anchors on max(Meeting Date)
    # = month-end), so the Summary states the number to reconcile the two.
    month_end = first_of_month + pd.offsets.MonthEnd(0)
    later_unpaid = int((
        (meeting_dt > anchor) & (meeting_dt <= month_end)
        & has_demand & nothing_collected
    ).sum())

    keys = normalise_account_key(eod_df[account_col])
    paid_lookup = set(paid_keys or ())
    is_paid = keys.isin(paid_lookup)
    not_paid_mask = to_collect_rows & ~is_paid

    demand_accounts = int(demand_mask.sum())
    ftod_accounts = int(ftod_rows.sum())
    collected_at_eod = demand_accounts - ftod_accounts
    today_demand_accounts = int(today_demand_mask.sum())
    today_collected_at_eod = today_demand_accounts - int(today_rows.sum())
    today_accounts = int(today_rows.sum())
    to_collect_accounts = int(to_collect_rows.sum())
    ftod_paid = int((ftod_rows & is_paid).sum())
    today_paid = int((today_rows & is_paid).sum())
    paid_accounts = ftod_paid + today_paid
    not_paid_accounts = int(not_paid_mask.sum())

    # The second segment is named by the day (or days) whose demand it holds —
    # today's date, not the EOD's. Normally the hourly runs the day after the
    # EOD, so it is a single day; a longer gap is spelt out as a range.
    day_after_eod = eod_date + pd.Timedelta(days=1)
    today_label = (
        f"Demand on {anchor.strftime('%d-%m-%Y')}" if day_after_eod >= anchor
        else f"Demand {day_after_eod.strftime('%d-%m-%Y')} to {anchor.strftime('%d-%m-%Y')}"
    )

    cols = [c for c in (source_columns or eod_df.columns) if c in eod_df.columns]
    out_df = eod_df.loc[not_paid_mask, cols].copy()
    # Which segment each listed client came from, so the two are separable in
    # the output as well as on the Summary sheet.
    out_df.insert(0, SEGMENT_COL, np.where(
        ftod_rows.loc[not_paid_mask],
        f"FTOD up to {eod_date.strftime('%d-%m-%Y')}",
        today_label,
    ))

    logger.info(
        "CLIENTS NOT PAID | window %s..%s (EOD %s) | FTOD carry-forward: %d "
        "(of %d demand) | demand after EOD: %d (of %d) | to collect: %d | paid "
        "in hourly: %d (%d + %d) | NOT PAID: %d",
        first_of_month.strftime('%d-%m-%Y'), anchor.strftime('%d-%m-%Y'),
        eod_date.strftime('%d-%m-%Y'), ftod_accounts, demand_accounts,
        today_accounts, today_demand_accounts, to_collect_accounts,
        paid_accounts, ftod_paid, today_paid, not_paid_accounts,
    )
    if to_collect_accounts - paid_accounts != not_paid_accounts:  # defensive; masks are disjoint
        logger.warning("CLIENTS NOT PAID: count identity failed — output may be incomplete")

    # Same wording on the Summary sheet: the segment is named by its own day.
    after_eod_label = today_label[len('Demand '):]
    summary_rows = [
        ('Hourly report date', anchor.strftime('%d-%m-%Y')),
        ('Hourly report time', report_time or ''),
        ('Latest EOD report date (FTOD measured here)', eod_date.strftime('%d-%m-%Y')),
        ('Demand window', f"{first_of_month.strftime('%d-%m-%Y')} to {anchor.strftime('%d-%m-%Y')}"),
        ('', ''),
        (f"— Up to the EOD ({eod_date.strftime('%d-%m-%Y')}) —", ''),
        ('Regular Demand accounts (report DEMAND)', demand_accounts),
        ('Collected as per latest EOD (report COLLECTION)', collected_at_eod),
        ('FTOD — not paid at latest EOD (report FTOD)', ftod_accounts),
        ('', ''),
        (f"— Demand {after_eod_label} —", ''),
        ('Regular Demand accounts', today_demand_accounts),
        ('Already collected in the EOD file', today_collected_at_eod),
        ('Not collected', today_accounts),
        ('', ''),
        ('To collect (FTOD + demand ' + after_eod_label + ')', to_collect_accounts),
        ('Paid in Hourly Collection Report', paid_accounts),
        ('   of which FTOD carry-forward', ftod_paid),
        ('   of which demand ' + after_eod_label, today_paid),
        ('Clients Not Paid (to collect - paid)', not_paid_accounts),
        ('', ''),
        ('Not listed: demand due later this month', later_unpaid),
        ('Hourly Collection rows read (after filters)', int(collection_rows)),
        ('Distinct accounts in Hourly Collection', int(collection_accounts)),
        ('Matched on', f"{account_col} (unique account id — names are not used)"),
        ('Details source', 'Regular Demand vs Collection (EOD output) — all columns, as-is'),
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_workbook(out_df, summary_rows, output_path)

    return {
        'generated': True,
        'path': str(output_path),
        'reportDate': anchor.strftime('%d-%m-%Y'),
        'eodReportDate': eod_date.strftime('%d-%m-%Y'),
        'demandAccounts': demand_accounts,
        'collectedAtEod': collected_at_eod,
        'ftodAccounts': ftod_accounts,
        'todayDemandAccounts': today_demand_accounts,
        'todayUnpaidAccounts': today_accounts,
        'toCollectAccounts': to_collect_accounts,
        'laterUnpaid': later_unpaid,
        'paidAccounts': paid_accounts,
        'ftodPaidAccounts': ftod_paid,
        'todayPaidAccounts': today_paid,
        'notPaidAccounts': not_paid_accounts,
        'columns': len(cols) + 1,  # + the Unpaid Segment tag column
    }
