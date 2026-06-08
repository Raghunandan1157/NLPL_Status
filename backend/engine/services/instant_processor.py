"""
Instant Report - Pivot Summary Engine (Optimized)
==================================================
Pre-filters by Meeting Date in Python, then runs DuckDB GROUP BY queries
on the smaller filtered DataFrame. Targets <1s total pivot computation.

VBA logic:
  All sections use Meeting Date filtered data (1st of month → target date)
  so that PNPA and NPA demand values vary correctly per date.
"""

import logging
import pandas as pd
import duckdb
from services.column_matcher import find_column


def compute_instant_report(df, target_date=None):
    con = duckdb.connect()

    # ── Resolve columns ──────────────────────────────────────────────
    col_region = find_column(df, 'Region', 'RegionName', 'Region Name') or 'Region'
    col_area = find_column(df, 'Area', 'AreaName', 'Area Name', 'District', 'DistrictName', 'District Name') or 'Area'
    col_branch = find_column(df, 'BranchName', 'Branch Name', 'Branch', 'Branchname') or 'BranchName'
    col_demand_count = find_column(df, 'No of Regular Demand', 'No. of Regular Demand', 'NoOfRegularDemand') or 'No of Regular Demand'
    col_cumulative = find_column(df, 'No of Cumulative', 'No. of Cumulative', 'NoOfCumulative') or 'No of Cumulative'
    col_dpd_group = find_column(df, 'DPD Group', 'DPDGroup') or 'DPD Group'
    col_dpd_last_month = find_column(df, 'DPD Group - Last Month', 'DPD Group - last Month') or 'DPD Group - Last Month'
    col_loan_status = find_column(df, 'Loan Status - Last Month', 'LoanStatus - Last Month') or 'Loan Status - Last Month'
    col_inst_collected = find_column(df, 'installment - collected value', 'Installment - Collected Value') or 'installment - collected value'
    col_dpd_days = find_column(df, 'DPD Days', 'DPDDays', 'DPD days')
    if not col_dpd_days:
        col_dpd_days = col_dpd_group
    col_collection = find_column(df, 'Collection', 'Collection_Sum') or 'Collection'
    col_meeting_date = find_column(df, 'Meeting Date', 'MeetingDate', 'Meeting date') or 'Meeting Date'

    levels = [('Region', col_region), ('Area', col_area), ('Branch', col_branch)]

    # ── Pre-filter by Meeting Date (Python — handles any date format) ─
    # All sections use date-filtered data
    if target_date and col_meeting_date in df.columns:
        first_of_month = target_date.replace(day=1)

        raw_vals = df[col_meeting_date]
        logging.info(f"Instant: Meeting Date column '{col_meeting_date}' dtype={raw_vals.dtype}, non-null={raw_vals.notna().sum()}/{len(raw_vals)}")

        # Sample values for debugging
        sample = raw_vals.dropna().head(5).tolist()
        logging.info(f"Instant: Meeting Date sample values: {sample}")

        # Try multiple parsing strategies
        meeting_dates = pd.Series([pd.NaT] * len(df), index=df.index)

        # Strategy 1: Already datetime
        if pd.api.types.is_datetime64_any_dtype(raw_vals):
            meeting_dates = raw_vals
            logging.info("Instant: Meeting Date already datetime")

        # Strategy 2: Numeric dtype (int/float) → Excel serial dates (days since 1899-12-30)
        elif pd.api.types.is_numeric_dtype(raw_vals):
            meeting_dates = pd.to_datetime(raw_vals, unit='D', origin='1899-12-30', errors='coerce')
            valid_count = meeting_dates.notna().sum()
            logging.info(f"Instant: Meeting Date parsed as Excel serial (dtype={raw_vals.dtype}): {valid_count}/{len(df)} valid")
            if valid_count > 0:
                sample_parsed = meeting_dates.dropna().head(3).tolist()
                logging.info(f"Instant: Meeting Date parsed sample: {sample_parsed}")

        else:
            # Strategy 3: String - try dayfirst=True (DD-MM-YYYY, DD/MM/YYYY, etc.)
            parsed = pd.to_datetime(raw_vals, dayfirst=True, errors='coerce')
            valid_count = parsed.notna().sum()
            if valid_count > 0:
                meeting_dates = parsed
                logging.info(f"Instant: Meeting Date parsed with dayfirst=True: {valid_count}/{len(df)} valid")
            else:
                # Strategy 4: Try without dayfirst (MM/DD/YYYY, YYYY-MM-DD, etc.)
                parsed = pd.to_datetime(raw_vals, errors='coerce')
                valid_count = parsed.notna().sum()
                if valid_count > 0:
                    meeting_dates = parsed
                    logging.info(f"Instant: Meeting Date parsed with default: {valid_count}/{len(df)} valid")

        valid_total = meeting_dates.notna().sum()
        if valid_total > 0:
            mask = (meeting_dates >= pd.Timestamp(first_of_month)) & (meeting_dates <= pd.Timestamp(target_date))
            df_dated = df[mask].copy()
            logging.info(f"Instant: Meeting Date filter {first_of_month.strftime('%d-%m-%Y')} to {target_date.strftime('%d-%m-%Y')}: {len(df_dated)}/{len(df)} rows")
        else:
            logging.warning("Instant: Could not parse any Meeting Date values, using all data")
            df_dated = df
    else:
        df_dated = df
        if target_date:
            logging.warning(f"Instant: '{col_meeting_date}' column not found in DataFrame columns: {list(df.columns[:10])}... skipping date filter")

    # Register both tables
    con.register('m', df_dated)     # date-filtered (sections 1-3)
    con.register('mall', df)        # all data (sections 4-5)

    sections = []

    # ── Section 1: Regular Demand vs Collection ──────────────────────
    s1 = []
    for lname, lcol in levels:
        try:
            rows = con.execute(f"""
                SELECT "{lcol}",
                    COALESCE(SUM(TRY_CAST("{col_demand_count}" AS BIGINT)), 0),
                    COALESCE(SUM(CASE WHEN CAST(COALESCE("{col_dpd_group}",'') AS VARCHAR) NOT LIKE '%1-30%'
                        THEN TRY_CAST("{col_demand_count}" AS BIGINT) ELSE 0 END), 0)
                FROM m GROUP BY "{lcol}" ORDER BY "{lcol}"
            """).fetchall()
            data_rows = []
            for r in rows:
                d, c = int(r[1]), int(r[2])
                data_rows.append({'name': str(r[0] or 'Unknown'), 'demand': d, 'collection': c,
                                  'ftod': d - c, 'collection_pct': round(c/d*100, 2) if d else 0})

            gt = con.execute(f"""
                SELECT COALESCE(SUM(TRY_CAST("{col_demand_count}" AS BIGINT)), 0),
                    COALESCE(SUM(CASE WHEN CAST(COALESCE("{col_dpd_group}",'') AS VARCHAR) NOT LIKE '%1-30%'
                        THEN TRY_CAST("{col_demand_count}" AS BIGINT) ELSE 0 END), 0)
                FROM m
            """).fetchone()
            td, tc = int(gt[0]), int(gt[1])
            s1.append({'level': lname, 'type': 'regular',
                'headers': ['Name', 'Regular Demand', 'Regular Collection', 'FTOD', 'Collection %'],
                'rows': data_rows,
                'grand_total': {'demand': td, 'collection': tc, 'ftod': td-tc,
                                'collection_pct': round(tc/td*100, 2) if td else 0}})
        except Exception as e:
            logging.warning(f"Section 1 - {lname}: {e}")
    sections.append({'title': 'Regular Demand vs Collection', 'tables': s1})

    # ── Sections 2-4: DPD Buckets ────────────────────────────────────
    bucket_defs = [
        ('1-30 DPD Bucket', '1-30', col_dpd_last_month, '1-30', 'm'),
        ('31-60 DPD Bucket', '31-60', col_dpd_last_month, '31-60', 'm'),
        ('PNPA (61-90 DPD)', 'PNPA', col_dpd_days, '61-90', 'm'),
    ]
    for title, prefix, fcol, pattern, tbl in bucket_defs:
        tables = []
        for lname, lcol in levels:
            try:
                rows = con.execute(f"""
                    SELECT "{lcol}",
                        COALESCE(SUM(TRY_CAST("{col_cumulative}" AS BIGINT)), 0),
                        COALESCE(SUM(CASE WHEN TRY_CAST("{col_inst_collected}" AS INT)=1 THEN 1 ELSE 0 END), 0)
                    FROM {tbl}
                    WHERE CAST(COALESCE("{fcol}",'') AS VARCHAR) LIKE '%{pattern}%'
                      AND LOWER(COALESCE(CAST("{col_loan_status}" AS VARCHAR),'')) LIKE '%active%'
                    GROUP BY "{lcol}" ORDER BY "{lcol}"
                """).fetchall()
                data_rows = []
                for r in rows:
                    d, c = int(r[1]), int(r[2])
                    data_rows.append({'name': str(r[0] or 'Unknown'), 'demand': d, 'collection': c,
                                      'balance': d-c, 'collection_pct': round(c/d*100, 2) if d else 0})

                gt = con.execute(f"""
                    SELECT COALESCE(SUM(TRY_CAST("{col_cumulative}" AS BIGINT)), 0),
                        COALESCE(SUM(CASE WHEN TRY_CAST("{col_inst_collected}" AS INT)=1 THEN 1 ELSE 0 END), 0)
                    FROM {tbl}
                    WHERE CAST(COALESCE("{fcol}",'') AS VARCHAR) LIKE '%{pattern}%'
                      AND LOWER(COALESCE(CAST("{col_loan_status}" AS VARCHAR),'')) LIKE '%active%'
                """).fetchone()
                td, tc = int(gt[0]), int(gt[1])
                tables.append({'level': lname, 'type': 'bucket',
                    'headers': ['Name', f'{prefix} Demand', f'{prefix} Collection', f'{prefix} Balance', 'Collection %'],
                    'rows': data_rows,
                    'grand_total': {'demand': td, 'collection': tc, 'balance': td-tc,
                                    'collection_pct': round(tc/td*100, 2) if td else 0}})
            except Exception as e:
                logging.warning(f"{title} - {lname}: {e}")
        sections.append({'title': title, 'tables': tables})

    # ── Section 5: NPA ───────────────────────────────────────────────
    npa_tables = []
    npa_where = f"""WHERE LOWER(COALESCE(CAST("{col_loan_status}" AS VARCHAR),'')) LIKE '%npa%'
        AND CAST(COALESCE("{col_dpd_last_month}",'') AS VARCHAR) NOT LIKE '%0 Days%'
        AND TRIM(CAST(COALESCE("{col_dpd_last_month}",'') AS VARCHAR)) != ''"""

    for lname, lcol in levels:
        try:
            rows = con.execute(f"""
                SELECT "{lcol}",
                    COALESCE(SUM(TRY_CAST("{col_cumulative}" AS BIGINT)), 0),
                    COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NOT NULL AND TRIM(CAST("{col_dpd_group}" AS VARCHAR))!=''
                        AND "{col_collection}" IS NOT NULL THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NOT NULL AND TRIM(CAST("{col_dpd_group}" AS VARCHAR))!=''
                        THEN COALESCE(TRY_CAST("{col_collection}" AS DOUBLE),0) ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN ("{col_dpd_group}" IS NULL OR TRIM(CAST("{col_dpd_group}" AS VARCHAR))='')
                        AND "{col_collection}" IS NOT NULL THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NULL OR TRIM(CAST("{col_dpd_group}" AS VARCHAR))=''
                        THEN COALESCE(TRY_CAST("{col_collection}" AS DOUBLE),0) ELSE 0 END), 0)
                FROM m {npa_where}
                GROUP BY "{lcol}" ORDER BY "{lcol}"
            """).fetchall()
            data_rows = [{'name': str(r[0] or 'Unknown'), 'demand': int(r[1]),
                'activation_account': int(r[2]), 'activation_amount': round(float(r[3])),
                'closure_account': int(r[4]), 'closure_amount': round(float(r[5]))} for r in rows]

            gt = con.execute(f"""
                SELECT COALESCE(SUM(TRY_CAST("{col_cumulative}" AS BIGINT)), 0),
                    COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NOT NULL AND TRIM(CAST("{col_dpd_group}" AS VARCHAR))!=''
                        AND "{col_collection}" IS NOT NULL THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NOT NULL AND TRIM(CAST("{col_dpd_group}" AS VARCHAR))!=''
                        THEN COALESCE(TRY_CAST("{col_collection}" AS DOUBLE),0) ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN ("{col_dpd_group}" IS NULL OR TRIM(CAST("{col_dpd_group}" AS VARCHAR))='')
                        AND "{col_collection}" IS NOT NULL THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NULL OR TRIM(CAST("{col_dpd_group}" AS VARCHAR))=''
                        THEN COALESCE(TRY_CAST("{col_collection}" AS DOUBLE),0) ELSE 0 END), 0)
                FROM m {npa_where}
            """).fetchone()

            npa_tables.append({'level': lname, 'type': 'npa',
                'headers': ['Name', 'Demand', 'Account', 'Amount', 'Account', 'Amount'],
                'header_groups': [{'label':'Name','colspan':1},{'label':'Demand','colspan':1},
                    {'label':'Activation','colspan':2},{'label':'Closure','colspan':2}],
                'rows': data_rows,
                'grand_total': {'demand': int(gt[0]), 'activation_account': int(gt[1]),
                    'activation_amount': round(float(gt[2])), 'closure_account': int(gt[3]),
                    'closure_amount': round(float(gt[4]))}})
        except Exception as e:
            logging.warning(f"NPA - {lname}: {e}")
    sections.append({'title': 'NPA', 'tables': npa_tables})

    # ── Product-Wise Reports (Region level only) ──────────────────────
    col_product = find_column(df, 'Product Name', 'ProductName', 'Product', 'Product name') or 'Product Name'

    # Products: IGL, FIG, IL (IL maps to VVY in data)
    products = [('IGL', 'IGL'), ('FIG', 'FIG'), ('IL', 'VVY')]

    if col_product in df.columns:
        for display_name, filter_val in products:
            # Filter data by product
            prod_mask = df[col_product].astype(str).str.strip() == filter_val
            df_prod_all = df[prod_mask]

            prod_dated_mask = df_dated[col_product].astype(str).str.strip() == filter_val if col_product in df_dated.columns else prod_mask
            df_prod_dated = df_dated[prod_dated_mask] if col_product in df_dated.columns else df_prod_all

            if len(df_prod_all) == 0 and len(df_prod_dated) == 0:
                continue

            con.register('mp', df_prod_dated)    # date-filtered product data
            con.register('mallp', df_prod_all)   # all product data

            prod_tables = []
            lcol = col_region  # Region level only

            # ── Product: Regular Demand vs Collection ─────────────
            try:
                rows = con.execute(f"""
                    SELECT "{lcol}",
                        COALESCE(SUM(TRY_CAST("{col_demand_count}" AS BIGINT)), 0),
                        COALESCE(SUM(CASE WHEN CAST(COALESCE("{col_dpd_group}",'') AS VARCHAR) NOT LIKE '%1-30%'
                            THEN TRY_CAST("{col_demand_count}" AS BIGINT) ELSE 0 END), 0)
                    FROM mp GROUP BY "{lcol}" ORDER BY "{lcol}"
                """).fetchall()
                data_rows = []
                for r in rows:
                    d, c = int(r[1]), int(r[2])
                    data_rows.append({'name': str(r[0] or 'Unknown'), 'demand': d, 'collection': c,
                                      'ftod': d - c, 'collection_pct': round(c/d*100, 2) if d else 0})
                gt = con.execute(f"""
                    SELECT COALESCE(SUM(TRY_CAST("{col_demand_count}" AS BIGINT)), 0),
                        COALESCE(SUM(CASE WHEN CAST(COALESCE("{col_dpd_group}",'') AS VARCHAR) NOT LIKE '%1-30%'
                            THEN TRY_CAST("{col_demand_count}" AS BIGINT) ELSE 0 END), 0)
                    FROM mp
                """).fetchone()
                td, tc = int(gt[0]), int(gt[1])
                prod_tables.append({'level': 'Region', 'type': 'regular',
                    'headers': ['Name', 'Regular Demand', 'Regular Collection', 'FTOD', 'Collection %'],
                    'rows': data_rows,
                    'grand_total': {'demand': td, 'collection': tc, 'ftod': td-tc,
                                    'collection_pct': round(tc/td*100, 2) if td else 0}})
            except Exception as e:
                logging.warning(f"Product {display_name} Regular: {e}")

            # ── Product: DPD Buckets (1-30, 31-60, PNPA) ─────────
            prod_bucket_defs = [
                ('1-30', col_dpd_last_month, '1-30', 'mp'),
                ('31-60', col_dpd_last_month, '31-60', 'mp'),
                ('PNPA', col_dpd_days, '61-90', 'mp'),
            ]
            for prefix, fcol, pattern, tbl in prod_bucket_defs:
                try:
                    rows = con.execute(f"""
                        SELECT "{lcol}",
                            COALESCE(SUM(TRY_CAST("{col_cumulative}" AS BIGINT)), 0),
                            COALESCE(SUM(CASE WHEN TRY_CAST("{col_inst_collected}" AS INT)=1 THEN 1 ELSE 0 END), 0)
                        FROM {tbl}
                        WHERE CAST(COALESCE("{fcol}",'') AS VARCHAR) LIKE '%{pattern}%'
                          AND LOWER(COALESCE(CAST("{col_loan_status}" AS VARCHAR),'')) LIKE '%active%'
                        GROUP BY "{lcol}" ORDER BY "{lcol}"
                    """).fetchall()
                    data_rows = []
                    for r in rows:
                        d, c = int(r[1]), int(r[2])
                        data_rows.append({'name': str(r[0] or 'Unknown'), 'demand': d, 'collection': c,
                                          'balance': d-c, 'collection_pct': round(c/d*100, 2) if d else 0})
                    gt = con.execute(f"""
                        SELECT COALESCE(SUM(TRY_CAST("{col_cumulative}" AS BIGINT)), 0),
                            COALESCE(SUM(CASE WHEN TRY_CAST("{col_inst_collected}" AS INT)=1 THEN 1 ELSE 0 END), 0)
                        FROM {tbl}
                        WHERE CAST(COALESCE("{fcol}",'') AS VARCHAR) LIKE '%{pattern}%'
                          AND LOWER(COALESCE(CAST("{col_loan_status}" AS VARCHAR),'')) LIKE '%active%'
                    """).fetchone()
                    td, tc = int(gt[0]), int(gt[1])
                    prod_tables.append({'level': 'Region', 'type': 'bucket',
                        'headers': ['Name', f'{prefix} Demand', f'{prefix} Collection', f'{prefix} Balance', 'Collection %'],
                        'rows': data_rows,
                        'grand_total': {'demand': td, 'collection': tc, 'balance': td-tc,
                                        'collection_pct': round(tc/td*100, 2) if td else 0}})
                except Exception as e:
                    logging.warning(f"Product {display_name} {prefix}: {e}")

            # ── Product: NPA ──────────────────────────────────────
            try:
                rows = con.execute(f"""
                    SELECT "{lcol}",
                        COALESCE(SUM(TRY_CAST("{col_cumulative}" AS BIGINT)), 0),
                        COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NOT NULL AND TRIM(CAST("{col_dpd_group}" AS VARCHAR))!=''
                            AND "{col_collection}" IS NOT NULL THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NOT NULL AND TRIM(CAST("{col_dpd_group}" AS VARCHAR))!=''
                            THEN COALESCE(TRY_CAST("{col_collection}" AS DOUBLE),0) ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN ("{col_dpd_group}" IS NULL OR TRIM(CAST("{col_dpd_group}" AS VARCHAR))='')
                            AND "{col_collection}" IS NOT NULL THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NULL OR TRIM(CAST("{col_dpd_group}" AS VARCHAR))=''
                            THEN COALESCE(TRY_CAST("{col_collection}" AS DOUBLE),0) ELSE 0 END), 0)
                    FROM mp {npa_where}
                    GROUP BY "{lcol}" ORDER BY "{lcol}"
                """).fetchall()
                data_rows = [{'name': str(r[0] or 'Unknown'), 'demand': int(r[1]),
                    'activation_account': int(r[2]), 'activation_amount': round(float(r[3])),
                    'closure_account': int(r[4]), 'closure_amount': round(float(r[5]))} for r in rows]
                gt = con.execute(f"""
                    SELECT COALESCE(SUM(TRY_CAST("{col_cumulative}" AS BIGINT)), 0),
                        COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NOT NULL AND TRIM(CAST("{col_dpd_group}" AS VARCHAR))!=''
                            AND "{col_collection}" IS NOT NULL THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NOT NULL AND TRIM(CAST("{col_dpd_group}" AS VARCHAR))!=''
                            THEN COALESCE(TRY_CAST("{col_collection}" AS DOUBLE),0) ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN ("{col_dpd_group}" IS NULL OR TRIM(CAST("{col_dpd_group}" AS VARCHAR))='')
                            AND "{col_collection}" IS NOT NULL THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN "{col_dpd_group}" IS NULL OR TRIM(CAST("{col_dpd_group}" AS VARCHAR))=''
                            THEN COALESCE(TRY_CAST("{col_collection}" AS DOUBLE),0) ELSE 0 END), 0)
                    FROM mp {npa_where}
                """).fetchone()
                prod_tables.append({'level': 'Region', 'type': 'npa',
                    'headers': ['Name', 'Demand', 'Account', 'Amount', 'Account', 'Amount'],
                    'header_groups': [{'label':'Name','colspan':1},{'label':'Demand','colspan':1},
                        {'label':'Activation','colspan':2},{'label':'Closure','colspan':2}],
                    'rows': data_rows,
                    'grand_total': {'demand': int(gt[0]), 'activation_account': int(gt[1]),
                        'activation_amount': round(float(gt[2])), 'closure_account': int(gt[3]),
                        'closure_amount': round(float(gt[4]))}})
            except Exception as e:
                logging.warning(f"Product {display_name} NPA: {e}")

            if prod_tables:
                sections.append({'title': f'Region Wise - {display_name} Report', 'tables': prod_tables})
                logging.info(f"Instant: Product {display_name} ({filter_val}): {len(df_prod_dated)} dated / {len(df_prod_all)} total rows, {len(prod_tables)} tables")
    else:
        logging.warning(f"Instant: Product column '{col_product}' not found, skipping product-wise reports")

    con.close()
    return {
        'sections': sections,
        'metadata': {
            'total_rows': len(df), 'total_columns': len(df.columns),
            'section_count': len(sections),
            'target_date': target_date.strftime('%d-%m-%Y') if target_date else None,
            'filtered_rows': len(df_dated) if target_date else len(df),
        }
    }
