"""
Analytics Engine — Computation layer for the Analytics dashboard.
All functions read from Instant Report cached data (report.json + parquets).
No new data processing pipelines are introduced.
"""

import logging
from datetime import datetime
from calendar import monthrange

import pandas as pd

from services import instant_cache
from services.column_matcher import find_column


# Ordered DPD buckets used across distribution / flow / slippage
DPD_BUCKETS = [
    'Current (0)', '1-30', '31-60', '61-90',
    '91-120', '121-180', '181-365', '365+',
]


# ── Helpers ───────────────────────────────────────────────────────────

_DPD_STRING_MAP = {
    '0 days':      'Current (0)',
    '1: 1-30':     '1-30',
    '2: 31-60':    '31-60',
    '3: 61-90':    '61-90',
    '4: 91-120':   '91-120',
    '5: 121-180':  '121-180',
    '6: 181-365':  '181-365',
    '7: >365 days': '365+',
}


def _dpd_bucket(val):
    """Map a DPD value (string label or numeric) to its bucket label."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None

    # Try string-label mapping first (handles "0 Days", "1: 1-30", etc.)
    s = str(val).strip().lower()
    mapped = _DPD_STRING_MAP.get(s)
    if mapped:
        return mapped

    # Partial match for variations like "0days", "1:1-30"
    for key, bucket in _DPD_STRING_MAP.items():
        if key in s or s in key:
            return bucket

    # Fallback: try numeric
    try:
        d = int(float(val))
    except (ValueError, TypeError):
        return None
    if d <= 0:
        return 'Current (0)'
    if d <= 30:
        return '1-30'
    if d <= 60:
        return '31-60'
    if d <= 90:
        return '61-90'
    if d <= 120:
        return '91-120'
    if d <= 180:
        return '121-180'
    if d <= 365:
        return '181-365'
    return '365+'


def _get_section_table(report_data, section_idx, level):
    """Return the first table in *section_idx* whose level matches."""
    sections = report_data.get('sections', [])
    if section_idx >= len(sections):
        return None
    for table in sections[section_idx].get('tables', []):
        if table.get('level') == level:
            return table
    return None


def _find_previous_date(date_str, cached_dates_iso):
    """
    Find a suitable comparison date (ideally from the previous month).
    Falls back to the most recent earlier date.
    """
    target_month = date_str[:7]  # 'YYYY-MM'
    prev_month_dates = [d for d in cached_dates_iso
                        if d < date_str and not d.startswith(target_month)]
    if prev_month_dates:
        return max(prev_month_dates)
    earlier = [d for d in cached_dates_iso if d < date_str]
    return max(earlier) if earlier else None


# ── KPI Dashboard ─────────────────────────────────────────────────────

def compute_dashboard_kpis(report_data, df_collection=None):
    """
    Return dict with 8 KPI values for the top-of-page metric strip.

    Keys: collection_pct, total_demand, total_collection, ftod,
          npa_accounts, collection_amount, best_region, worst_region
    """
    kpis = {
        'collection_pct': 0,
        'total_demand': 0,
        'total_collection': 0,
        'ftod': 0,
        'npa_accounts': 0,
        'collection_amount': 0,
        'best_region': {'name': '—', 'collection_pct': 0},
        'worst_region': {'name': '—', 'collection_pct': 0},
    }

    # Section 0: Regular Demand vs Collection — Region grand_total
    sec0 = _get_section_table(report_data, 0, 'Region')
    if sec0:
        gt = sec0.get('grand_total', {})
        kpis['collection_pct'] = gt.get('collection_pct', 0)
        kpis['total_demand'] = gt.get('demand', 0)
        kpis['total_collection'] = gt.get('collection', 0)
        kpis['ftod'] = gt.get('ftod', 0)

        rows = sec0.get('rows', [])
        if rows:
            best = max(rows, key=lambda r: r.get('collection_pct', 0))
            worst = min(rows, key=lambda r: r.get('collection_pct', 0))
            kpis['best_region'] = {
                'name': best.get('name', ''),
                'collection_pct': best.get('collection_pct', 0),
            }
            kpis['worst_region'] = {
                'name': worst.get('name', ''),
                'collection_pct': worst.get('collection_pct', 0),
            }

    # Section 4: NPA grand_total demand = NPA account count
    sec4 = _get_section_table(report_data, 4, 'Region')
    if sec4:
        kpis['npa_accounts'] = sec4.get('grand_total', {}).get('demand', 0)

    # Collection amount (rupees) from collection parquet
    if df_collection is not None and len(df_collection) > 0:
        col = find_column(df_collection, 'CollectionTotal', 'Collection Total', 'Collection')
        if col:
            kpis['collection_amount'] = float(df_collection[col].sum())

    return kpis


# ── Rankings ──────────────────────────────────────────────────────────

def _rollup_rows_by_state(rows, region_to_state):
    """Aggregate company Region rows into true-State rows (KARNATAKA, ANDHRA
    PRADESH, …). Region names absent from the map pass through unchanged."""
    def _num(v):
        try:
            return float(v) if v not in (None, '') else 0.0
        except (TypeError, ValueError):
            return 0.0
    agg = {}
    for r in rows:
        region = str(r.get('name', '')).strip()
        state = region_to_state.get(region) or region_to_state.get(region.upper()) or region
        a = agg.get(state)
        if a is None:
            a = {'name': state, 'demand': 0.0, 'collection': 0.0, 'ftod': 0.0}
            agg[state] = a
        a['demand'] += _num(r.get('demand'))
        a['collection'] += _num(r.get('collection'))
        a['ftod'] += _num(r.get('ftod'))
    for a in agg.values():
        a['demand'] = int(a['demand'])
        a['collection'] = int(a['collection'])
        a['ftod'] = int(a['ftod'])
        a['collection_pct'] = round(100.0 * a['collection'] / a['demand'], 2) if a['demand'] else 0
    return list(agg.values())


def compute_rankings(report_data, level='Region', n=5, region_to_state=None):
    """
    Top *n* and bottom *n* entities by collection % at *level*.
    Also returns all rows sorted descending for the bar chart.

    level='State' rolls the company Region grouping up to true states using
    region_to_state (so Karnataka districts collapse into KARNATAKA).
    """
    if level == 'State':
        sec0 = _get_section_table(report_data, 0, 'Region')
        rows = _rollup_rows_by_state(list(sec0.get('rows', [])) if sec0 else [], region_to_state or {})
        sorted_desc = sorted(rows, key=lambda r: r.get('collection_pct', 0), reverse=True)
        sorted_asc = sorted(rows, key=lambda r: r.get('collection_pct', 0))
        return {'top': sorted_desc[:n], 'bottom': sorted_asc[:n], 'all': sorted_desc}

    sec0 = _get_section_table(report_data, 0, level)
    if not sec0:
        return {'top': [], 'bottom': [], 'all': []}

    rows = list(sec0.get('rows', []))
    sorted_desc = sorted(rows, key=lambda r: r.get('collection_pct', 0), reverse=True)
    sorted_asc = sorted(rows, key=lambda r: r.get('collection_pct', 0))

    return {
        'top': sorted_desc[:n],
        'bottom': sorted_asc[:n],
        'all': sorted_desc,
    }


# ── Trend Engine ──────────────────────────────────────────────────────

def compute_trends(date_reports_dict, entity_name='NLPL', entity_level='Region'):
    """
    Collection % for *entity_name* across all dates, with delta and 3-day MA.
    """
    sorted_dates = sorted(date_reports_dict.keys())
    results = []
    prev_pct = None
    pcts = []

    is_grand_total = entity_name.strip().upper() in ('NLPL', 'GRAND TOTAL')

    for date_str in sorted_dates:
        report = date_reports_dict[date_str]

        if is_grand_total:
            sec0 = _get_section_table(report, 0, entity_level)
            pct = sec0.get('grand_total', {}).get('collection_pct', 0) if sec0 else 0
        else:
            entity_data = instant_cache.extract_entity_data(
                report, entity_name, entity_level
            )
            sec0_data = entity_data.get('Regular Demand vs Collection', {})
            pct = sec0_data.get('collection_pct', 0)

        delta = round(pct - prev_pct, 2) if prev_pct is not None else 0
        pcts.append(pct)

        window = pcts[-3:]
        ma3 = round(sum(window) / len(window), 2)

        results.append({
            'date': date_str,
            'collection_pct': pct,
            'delta': delta,
            'ma3': ma3,
        })
        prev_pct = pct

    return results


# ── Run-Rate Projection ──────────────────────────────────────────────

def compute_projection(trend_data):
    """
    Project month-end collection % based on current pace within the month.
    """
    if not trend_data:
        return {
            'current_pct': 0, 'projected_pct': 0,
            'remaining_days': 0, 'daily_avg': 0,
        }

    # Identify current month from the latest date in trend data
    last_date_str = trend_data[-1]['date']
    last_date = datetime.strptime(last_date_str, '%Y-%m-%d')
    current_month = last_date_str[:7]

    month_data = [t for t in trend_data if t['date'].startswith(current_month)]

    current_pct = month_data[-1]['collection_pct'] if month_data else trend_data[-1]['collection_pct']

    if len(month_data) >= 2:
        total_gain = month_data[-1]['collection_pct'] - month_data[0]['collection_pct']
        days_span = len(month_data) - 1
        daily_avg = total_gain / days_span if days_span > 0 else 0
    else:
        daily_avg = 0

    _, total_days = monthrange(last_date.year, last_date.month)
    remaining_days = total_days - last_date.day

    projected_pct = min(100.0, current_pct + (daily_avg * remaining_days))

    return {
        'current_pct': round(current_pct, 2),
        'projected_pct': round(projected_pct, 2),
        'remaining_days': remaining_days,
        'daily_avg': round(daily_avg, 2),
    }


# ── DPD Distribution ─────────────────────────────────────────────────

def compute_dpd_distribution(df_par):
    """
    Count accounts in each DPD bucket from the PAR parquet.
    """
    if df_par is None or len(df_par) == 0:
        return []

    dpd_col = find_column(df_par, 'DPD Days', 'DPDDays', 'Days Group', 'DPD Group')
    if not dpd_col:
        return []

    buckets = df_par[dpd_col].apply(_dpd_bucket)
    counts = buckets.value_counts()

    return [
        {'bucket': b, 'count': int(counts.get(b, 0))}
        for b in DPD_BUCKETS
    ]


# ── DPD Flow / Migration Matrix ──────────────────────────────────────

def compute_dpd_flow(df_par_current, df_par_previous):
    """
    Cross-tab of previous-bucket vs current-bucket for accounts present
    in both dates. Rows = previous bucket, Cols = current bucket.
    """
    empty = {'buckets': DPD_BUCKETS, 'matrix': [], 'previous_date': None}

    if df_par_current is None or df_par_previous is None:
        return empty
    if len(df_par_current) == 0 or len(df_par_previous) == 0:
        return empty

    id_curr = find_column(df_par_current, 'AccountID', 'Account ID', 'AccountId')
    id_prev = find_column(df_par_previous, 'AccountID', 'Account ID', 'AccountId')
    dpd_curr = find_column(df_par_current, 'DPD Days', 'DPDDays', 'Days Group')
    dpd_prev = find_column(df_par_previous, 'DPD Days', 'DPDDays', 'Days Group')

    if not all([id_curr, id_prev, dpd_curr, dpd_prev]):
        return empty

    curr = df_par_current[[id_curr, dpd_curr]].copy()
    prev = df_par_previous[[id_prev, dpd_prev]].copy()
    curr.columns = ['account_id', 'dpd_now']
    prev.columns = ['account_id', 'dpd_prev']

    curr['bucket_now'] = curr['dpd_now'].apply(_dpd_bucket)
    prev['bucket_prev'] = prev['dpd_prev'].apply(_dpd_bucket)

    merged = prev.merge(curr, on='account_id', how='inner')

    matrix = []
    for rb in DPD_BUCKETS:
        row = []
        mask_r = merged['bucket_prev'] == rb
        for cb in DPD_BUCKETS:
            row.append(int((mask_r & (merged['bucket_now'] == cb)).sum()))
        matrix.append(row)

    return {'buckets': DPD_BUCKETS, 'matrix': matrix}


# ── Fresh Slippage ────────────────────────────────────────────────────

def compute_fresh_slippage(df_par_current, df_par_previous):
    """
    Accounts that were Current (0 DPD) in *previous* and slipped to 1-30 in *current*.
    Returns overall metrics (parquet has no region column).
    """
    empty = {'slipped': 0, 'total_current_prev': 0, 'rate': 0}

    if df_par_current is None or df_par_previous is None:
        return empty

    id_curr = find_column(df_par_current, 'AccountID', 'Account ID', 'AccountId')
    id_prev = find_column(df_par_previous, 'AccountID', 'Account ID', 'AccountId')
    dpd_curr = find_column(df_par_current, 'DPD Days', 'DPDDays', 'Days Group')
    dpd_prev = find_column(df_par_previous, 'DPD Days', 'DPDDays', 'Days Group')

    if not all([id_curr, id_prev, dpd_curr, dpd_prev]):
        return empty

    curr = df_par_current[[id_curr, dpd_curr]].copy()
    prev = df_par_previous[[id_prev, dpd_prev]].copy()
    curr.columns = ['account_id', 'dpd_now']
    prev.columns = ['account_id', 'dpd_prev']

    curr['bucket_now'] = curr['dpd_now'].apply(_dpd_bucket)
    prev['bucket_prev'] = prev['dpd_prev'].apply(_dpd_bucket)

    merged = prev.merge(curr, on='account_id', how='inner')

    total_current_prev = int((merged['bucket_prev'] == 'Current (0)').sum())
    slipped = int(
        ((merged['bucket_prev'] == 'Current (0)') & (merged['bucket_now'] == '1-30')).sum()
    )
    rate = round(slipped / total_current_prev * 100, 2) if total_current_prev > 0 else 0

    # Also compute breakdown by what they slipped to (all buckets)
    current_prev_mask = merged['bucket_prev'] == 'Current (0)'
    breakdown = []
    for b in DPD_BUCKETS[1:]:  # skip 'Current (0)'
        cnt = int((current_prev_mask & (merged['bucket_now'] == b)).sum())
        if cnt > 0:
            breakdown.append({'bucket': b, 'count': cnt})

    return {
        'slipped': slipped,
        'total_current_prev': total_current_prev,
        'rate': rate,
        'stayed_current': int((current_prev_mask & (merged['bucket_now'] == 'Current (0)')).sum()),
        'breakdown': breakdown,
    }


# ── Heatmap ───────────────────────────────────────────────────────────

def compute_heatmap(date_reports_dict, level='Region'):
    """
    Entity x Date matrix of collection percentages for heatmap display.
    """
    sorted_dates = sorted(date_reports_dict.keys())
    entity_set = set()

    # Collect all entity names across dates
    for report in date_reports_dict.values():
        table = _get_section_table(report, 0, level)
        if table:
            for row in table.get('rows', []):
                name = row.get('name', '').strip()
                if name:
                    entity_set.add(name)

    entities = sorted(entity_set)

    # Build values matrix and compute averages for sorting
    values = []
    averages = []
    for entity in entities:
        row_vals = []
        for date_str in sorted_dates:
            report = date_reports_dict[date_str]
            table = _get_section_table(report, 0, level)
            pct = None
            if table:
                for row in table.get('rows', []):
                    if row.get('name', '').strip() == entity:
                        pct = row.get('collection_pct')
                        break
            row_vals.append(pct)
        values.append(row_vals)
        valid = [v for v in row_vals if v is not None]
        averages.append(round(sum(valid) / len(valid), 2) if valid else 0)

    return {
        'entities': entities,
        'dates': sorted_dates,
        'values': values,
        'averages': averages,
    }
