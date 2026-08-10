"""Isolated test for the Hourly "Clients Not Paid" extract.

Runs against synthetic frames in a throwaway dir (no Flask/DuckDB needed) and
verifies the rules the feature is specified on:
  - the demand base = Meeting Date within first-of-month..report date AND a
    regular demand instalment  (the report's DEMAND column)
  - FTOD = the demand rows still in DPD Group "1-30", i.e. not collected at the
    last EOD  (the report's FTOD column = DEMAND - COLLECTION)
  - demand falling due AFTER the EOD date (today's) joins the same file, judged
    on the EOD workbook's own Collection value since no EOD has assessed it
  - to collect = FTOD carry-forward + that demand; paid in the hourly file is
    subtracted from both, and each listed row is tagged with its segment
  - an account paying MULTIPLE times counts once, and counts as paid
  - a zero / reversed-out payment is NOT a payment
  - matching is on the account id, tolerant of int/float/whitespace formatting
  - the identity  FTOD accounts - paid accounts = clients not paid  holds
  - the source frame is never mutated and blank fields stay blank
  - missing optional columns degrade instead of raising (the hourly run must
    never be broken by this additional step)
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE.parent / "backend" / "engine"))

from services.clients_not_paid import (  # noqa: E402
    SEGMENT_COL,
    build_clients_not_paid,
    normalise_account_key,
)

FAILS = []


def check(cond, msg):
    print(("  OK  " if cond else " FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def sample_eod():
    """Six accounts, report date 05-06-2026. Demand base = 101, 102, 105, 106
    (103's meeting date is still ahead, 104 has no regular demand); of those,
    106 was collected at EOD (DPD Group "0 Days"), so FTOD = 101, 102, 105.
    """
    return pd.DataFrame({
        "Account ID": [101, 102, 103, 104, 105, 106],
        "Client Name": ["A", "B", "C", "D", "E", None],
        "Meeting Date": ["02-06-2026", "03-06-2026", "20-06-2026",
                         "03-06-2026", "03-06-2026", "03-06-2026"],
        "No of Regular Demand": [1, 1, 1, 0, 1, 1],
        "Regular Demand": [1000, 2000, 3000, 0, 4000, 5000],
        "DPD Group": ["1: 1-30", "1: 1-30", "1: 1-30", "1: 1-30",
                      "1: 1-30", "0 Days"],
    })


def paid_keys_from_collection(coll):
    """Mirror how the hourly blueprint derives the paid set: one row per
    normalised account id, paid = a positive total."""
    amt = pd.to_numeric(coll["CollectionTotal"], errors="coerce")
    pivot = amt.groupby(normalise_account_key(coll["AccountID"])).sum(min_count=1)
    return set(pivot.index[pivot.fillna(0) > 0])


def main():
    tmp = Path(tempfile.mkdtemp(prefix="nlpl_cnp_"))
    report_date = pd.Timestamp("2026-06-05")

    print("> core matching, duplicates and zero payments")
    eod = sample_eod()
    before = eod.copy(deep=True)
    collection = pd.DataFrame({
        # 101 appears twice (one empty receipt, one real payment) -> PAID once
        # 105 only has zero-value rows -> NOT paid
        "AccountID": [101, 101, 102, 105.0, "105", 105],
        "CollectionTotal": [0, 1500, 2000, 0, 0, 0],
    })
    out = tmp / "Clients_Not_Paid.xlsx"
    stats = build_clients_not_paid(
        eod, account_col="Account ID", paid_keys=paid_keys_from_collection(collection),
        report_date=report_date, output_path=out, source_columns=list(eod.columns),
        report_time="12:30 PM", collection_rows=len(collection),
        collection_accounts=collection["AccountID"].nunique(),
    )
    data = pd.read_excel(out, sheet_name="Clients Not Paid")

    check(stats["demandAccounts"] == 4,
          f"DEMAND excludes future meeting date and zero regular demand (got {stats['demandAccounts']})")
    check(stats["collectedAtEod"] == 1 and stats["ftodAccounts"] == 3,
          f"FTOD = demand still in DPD 1-30 (got {stats['ftodAccounts']}, "
          f"collected {stats['collectedAtEod']})")
    check(stats["demandAccounts"] - stats["collectedAtEod"] == stats["ftodAccounts"],
          "DEMAND - COLLECTION = FTOD (ties to the report's OverAll row)")
    check(stats["paidAccounts"] == 2,
          f"duplicate payment rows count the account once (got {stats['paidAccounts']})")
    check(stats["notPaidAccounts"] == 1 and set(data["Account ID"]) == {105},
          f"zero-value payment is not a payment (got {sorted(data['Account ID'])})")
    check(stats["ftodAccounts"] - stats["paidAccounts"] == stats["notPaidAccounts"],
          "FTOD - paid = not paid")
    check(eod.equals(before), "source EOD frame is not mutated")
    check(106 not in set(data["Account ID"]),
          "an account already collected at EOD is never listed")
    check(list(data.columns) == [SEGMENT_COL] + list(eod.columns),
          "every source column is carried through, in order, behind the segment tag")

    print("> account ids match across int / float / whitespace formatting")
    eod2 = sample_eod()
    eod2["Account ID"] = ["101.0", " 102 ", 103, 104, 105, 106]
    stats2 = build_clients_not_paid(
        eod2, account_col="Account ID", paid_keys=paid_keys_from_collection(collection),
        report_date=report_date, output_path=tmp / "t2.xlsx",
        source_columns=list(eod2.columns),
    )
    check(stats2["paidAccounts"] == 2, f"ids still match (got {stats2['paidAccounts']})")

    print("> report date in a month the workbook doesn't hold is auto-corrected")
    stats3 = build_clients_not_paid(
        sample_eod(), account_col="Account ID", paid_keys=set(),
        report_date=pd.Timestamp("2026-09-05"), output_path=tmp / "t3.xlsx",
    )
    check(stats3["reportDate"] == "05-06-2026",
          f"anchored on the data's month (got {stats3['reportDate']})")

    print("> degrades instead of raising when optional columns are missing")
    no_count = sample_eod().drop(columns=["No of Regular Demand"])
    stats4 = build_clients_not_paid(
        no_count, account_col="Account ID", paid_keys=set(), report_date=report_date,
        output_path=tmp / "t4.xlsx", source_columns=list(no_count.columns),
    )
    check(stats4["demandAccounts"] == 5,
          f"without a demand count, the whole window is the demand base (got {stats4['demandAccounts']})")
    no_meeting = sample_eod().drop(columns=["Meeting Date"])
    check(build_clients_not_paid(
        no_meeting, account_col="Account ID", paid_keys=set(), report_date=report_date,
        output_path=tmp / "t5.xlsx") is None,
        "without Meeting Date the step skips (returns None) instead of raising")

    print("> FTOD is measured at the EOD's date; today's demand is added to it")
    # An hourly run the day AFTER the EOD must still report the EOD's FTOD as
    # the carry-forward, and list today's demand alongside it. 107 has demand
    # dated 06-06 (after the 05-06 EOD): FTOD stays 3, 107 joins as today's.
    dated = sample_eod()
    dated["Collection Date"] = ["05-06-2026", "05-06-2026", None, None, None, "05-06-2026"]
    dated = pd.concat([dated, pd.DataFrame([{
        "Account ID": 107, "Client Name": "F", "Meeting Date": "06-06-2026",
        "No of Regular Demand": 1, "Regular Demand": 6000, "DPD Group": "1: 1-30",
        "Collection Date": None,
    }])], ignore_index=True)

    for label, run_date, declared in [
        ("data-derived, no marker", pd.Timestamp("2026-06-06"), None),
        ("marker agrees", pd.Timestamp("2026-06-06"), pd.Timestamp("2026-06-05")),
        ("marker ahead of data -> data wins", pd.Timestamp("2026-06-07"),
         pd.Timestamp("2026-06-06")),
    ]:
        st = build_clients_not_paid(
            dated, account_col="Account ID", paid_keys=set(), report_date=run_date,
            output_path=tmp / "t8.xlsx", eod_report_date=declared)
        rows = pd.read_excel(tmp / "t8.xlsx", sheet_name="Clients Not Paid")
        check(st["eodReportDate"] == "05-06-2026" and st["ftodAccounts"] == 3,
              f"{label}: FTOD still anchored on the EOD (got {st['eodReportDate']}, "
              f"FTOD {st['ftodAccounts']})")
        check(st["todayUnpaidAccounts"] == 1 and 107 in set(rows["Account ID"]),
              f"{label}: demand due after the EOD is listed too "
              f"(got {st['todayUnpaidAccounts']})")
        check(st["toCollectAccounts"] == 4 and st["notPaidAccounts"] == 4,
              f"{label}: to collect = FTOD + demand since (got {st['toCollectAccounts']})")
        tags = set(rows[SEGMENT_COL])
        # The second segment is named by the day its demand is due — the run's
        # own date — not by the EOD's date. A run two days after the EOD (the
        # 07-06 case) covers both days, so it is spelt out as a range.
        expect_today = ("Demand on 06-06-2026" if run_date.day == 6
                        else "Demand 06-06-2026 to 07-06-2026")
        check(tags == {"FTOD up to 05-06-2026", expect_today},
              f"{label}: rows are tagged with the demand's own date "
              f"(got {sorted(tags)})")

    print("> today's demand already collected in the EOD file is excluded")
    # 107 shows a collection in the EOD workbook -> nothing left to collect for
    # it, so only the FTOD carry-forward remains.
    prepaid = dated.copy()
    prepaid["Collection"] = [None, None, None, None, None, 5000, 6000]
    st = build_clients_not_paid(
        prepaid, account_col="Account ID", paid_keys=set(),
        report_date=pd.Timestamp("2026-06-06"), output_path=tmp / "t10.xlsx")
    check(st["todayDemandAccounts"] == 1 and st["todayUnpaidAccounts"] == 0
          and st["notPaidAccounts"] == 3,
          f"already-collected demand after the EOD is not listed "
          f"(got {st['todayUnpaidAccounts']} of {st['todayDemandAccounts']})")

    print("> paying today's demand in the hourly file removes it")
    st = build_clients_not_paid(
        dated, account_col="Account ID", paid_keys={"107", "101"},
        report_date=pd.Timestamp("2026-06-06"), output_path=tmp / "t11.xlsx")
    rows = pd.read_excel(tmp / "t11.xlsx", sheet_name="Clients Not Paid")
    check(st["ftodPaidAccounts"] == 1 and st["todayPaidAccounts"] == 1
          and st["paidAccounts"] == 2 and st["notPaidAccounts"] == 2
          and set(rows["Account ID"]) == {102, 105},
          f"hourly payments clear both segments (got {sorted(rows['Account ID'])})")

    no_cd = dated.drop(columns=["Collection Date"])
    st = build_clients_not_paid(
        no_cd, account_col="Account ID", paid_keys=set(),
        report_date=pd.Timestamp("2026-06-07"), output_path=tmp / "t9.xlsx",
        eod_report_date=pd.Timestamp("2026-06-05"))
    check(st["eodReportDate"] == "05-06-2026",
          f"no Collection Date: falls back to the marker (got {st['eodReportDate']})")
    # 103 (20-06) is unpaid but its meeting date has not arrived yet, so it is
    # reported as a count and never listed; 107 (06-06) is inside the window.
    check(st["laterUnpaid"] == 1,
          f"demand due later this month is counted, not listed "
          f"(got {st['laterUnpaid']})")

    print("> boundary cases")
    stats6 = build_clients_not_paid(
        sample_eod(), account_col="Account ID", paid_keys=set(), report_date=report_date,
        output_path=tmp / "t6.xlsx")
    check(stats6["notPaidAccounts"] == 3, "nobody paid -> every FTOD account listed")
    stats7 = build_clients_not_paid(
        sample_eod(), account_col="Account ID", paid_keys={"101", "102", "105"},
        report_date=report_date, output_path=tmp / "t7.xlsx")
    empty = pd.read_excel(tmp / "t7.xlsx", sheet_name="Clients Not Paid")
    check(stats7["notPaidAccounts"] == 0 and len(empty) == 0 and len(empty.columns) == 7,
          "everybody paid -> a valid empty workbook that still has its headers")

    summary = pd.read_excel(out, sheet_name="Summary", header=None)
    labels = summary[0].astype(str).tolist()
    check("Regular Demand accounts (report DEMAND)" in labels and
          "FTOD — not paid at latest EOD (report FTOD)" in labels and
          "Paid in Hourly Collection Report" in labels and
          any(l.startswith("To collect (FTOD + demand") for l in labels) and
          "Clients Not Paid (to collect - paid)" in labels,
          "Summary sheet restates DEMAND / COLLECTION / FTOD and both segments")

    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}):")
        for m in FAILS:
            print("  -", m)
        sys.exit(1)
    print("SUCCESS: Clients Not Paid matches FTOD against the hourly collection correctly.")


if __name__ == "__main__":
    main()
