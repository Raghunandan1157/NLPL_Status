"""
Analytics Blueprint — API endpoints for the Analytics dashboard.
All data comes from Instant Report cached data (read-only).
"""

import logging

import pandas as pd
from flask import Blueprint, jsonify, request, send_from_directory

import config
from services import instant_cache
from services import analytics_engine

analytics_bp = Blueprint('analytics', __name__)
STATIC_ANALYTICS_DIR = config.STATIC_DIR / 'analytics'


def _region_state_map(date_str):
    """Map each company Region to its true State (from the demand master), so
    the analytics 'State' level can roll district-named regions (TUMKUR,
    KALABURAGI, …) up to KARNATAKA etc. Returns {} if unavailable."""
    try:
        dcache = config.BACKEND_MONTHLY_DIR / date_str[:7] / 'demand_cache.parquet'
        if not dcache.exists():
            return {}
        df = pd.read_parquet(dcache, columns=['State', 'Region']).dropna()
        out = {}
        for region, grp in df.groupby('Region'):
            mode = grp['State'].astype(str).str.strip().mode()
            out[str(region).strip()] = mode.iloc[0] if len(mode) else str(region).strip()
        return out
    except Exception:
        return {}


# ── Static files ──────────────────────────────────────────────────────

@analytics_bp.route('/')
def index():
    return send_from_directory(str(STATIC_ANALYTICS_DIR), 'index.html')


@analytics_bp.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(str(STATIC_ANALYTICS_DIR), filename)


# ── API: Available dates ──────────────────────────────────────────────

@analytics_bp.route('/api/dates')
def api_dates():
    try:
        dates = instant_cache.list_cached_dates()
        return jsonify({'dates': dates})
    except Exception as e:
        logging.error(f"Analytics dates error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Hierarchy ────────────────────────────────────────────────────

@analytics_bp.route('/api/hierarchy')
def api_hierarchy():
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400
    try:
        hierarchy = instant_cache.get_hierarchy_from_parquet(date_str)
        return jsonify(hierarchy)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logging.error(f"Analytics hierarchy error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Dashboard KPIs ──────────────────────────────────────────────

@analytics_bp.route('/api/dashboard')
def api_dashboard():
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400
    try:
        report = instant_cache.load_date_report(date_str)

        # Load collection parquet for amount metric
        df_collection = None
        coll_path = config.INSTANT_HISTORY_DIR / date_str / 'collection.parquet'
        if coll_path.exists():
            try:
                df_collection = pd.read_parquet(coll_path, columns=['AccountID', 'CollectionTotal'])
            except Exception:
                try:
                    df_collection = pd.read_parquet(coll_path)
                except Exception:
                    pass

        kpis = analytics_engine.compute_dashboard_kpis(report, df_collection)
        return jsonify(kpis)

    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logging.error(f"Analytics dashboard error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Rankings ─────────────────────────────────────────────────────

@analytics_bp.route('/api/rankings')
def api_rankings():
    date_str = request.args.get('date')
    level = request.args.get('level', 'Region')
    n = request.args.get('n', 5, type=int)
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400
    try:
        report = instant_cache.load_date_report(date_str)
        r2s = _region_state_map(date_str) if level == 'State' else None
        rankings = analytics_engine.compute_rankings(report, level=level, n=n, region_to_state=r2s)
        return jsonify(rankings)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logging.error(f"Analytics rankings error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Trends ───────────────────────────────────────────────────────

@analytics_bp.route('/api/trends')
def api_trends():
    entity = request.args.get('entity', 'NLPL')
    level = request.args.get('level', 'Region')
    try:
        cached_dates = instant_cache.list_cached_dates()
        date_list = [d['date_iso'] for d in cached_dates if d.get('has_report')]
        if not date_list:
            return jsonify([])

        date_reports = instant_cache.load_multi_date_reports(date_list)
        trends = analytics_engine.compute_trends(date_reports, entity_name=entity, entity_level=level)
        return jsonify(trends)

    except Exception as e:
        logging.error(f"Analytics trends error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Projection ──────────────────────────────────────────────────

@analytics_bp.route('/api/projection')
def api_projection():
    try:
        entity = request.args.get('entity', 'NLPL')
        level = request.args.get('level', 'Region')

        cached_dates = instant_cache.list_cached_dates()
        date_list = [d['date_iso'] for d in cached_dates if d.get('has_report')]
        if not date_list:
            return jsonify({'current_pct': 0, 'projected_pct': 0,
                            'remaining_days': 0, 'daily_avg': 0})

        date_reports = instant_cache.load_multi_date_reports(date_list)
        trends = analytics_engine.compute_trends(date_reports, entity_name=entity, entity_level=level)
        projection = analytics_engine.compute_projection(trends)
        return jsonify(projection)

    except Exception as e:
        logging.error(f"Analytics projection error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: DPD Distribution ────────────────────────────────────────────

@analytics_bp.route('/api/dpd-dist')
def api_dpd_dist():
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400
    try:
        par_path = config.INSTANT_HISTORY_DIR / date_str / 'par.parquet'
        if not par_path.exists():
            return jsonify({'error': f'No PAR data for {date_str}'}), 404

        try:
            df_par = pd.read_parquet(par_path, columns=['AccountID', 'DPD Days', 'LoanStatus'])
        except Exception:
            df_par = pd.read_parquet(par_path)
        dist = analytics_engine.compute_dpd_distribution(df_par)
        return jsonify(dist)

    except Exception as e:
        logging.error(f"Analytics DPD dist error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: DPD Flow / Migration ────────────────────────────────────────

@analytics_bp.route('/api/dpd-flow')
def api_dpd_flow():
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400
    try:
        cached_dates = instant_cache.list_cached_dates()
        all_dates = sorted(d['date_iso'] for d in cached_dates)

        prev_date = analytics_engine._find_previous_date(date_str, all_dates)
        if not prev_date:
            return jsonify({
                'buckets': analytics_engine.DPD_BUCKETS,
                'matrix': [],
                'previous_date': None,
                'message': 'No earlier date available for comparison',
            })

        par_curr_path = config.INSTANT_HISTORY_DIR / date_str / 'par.parquet'
        par_prev_path = config.INSTANT_HISTORY_DIR / prev_date / 'par.parquet'

        if not par_curr_path.exists() or not par_prev_path.exists():
            return jsonify({'error': 'PAR parquet missing for one or both dates'}), 404

        try:
            df_curr = pd.read_parquet(par_curr_path, columns=['AccountID', 'DPD Days', 'LoanStatus'])
            df_prev = pd.read_parquet(par_prev_path, columns=['AccountID', 'DPD Days', 'LoanStatus'])
        except Exception:
            df_curr = pd.read_parquet(par_curr_path)
            df_prev = pd.read_parquet(par_prev_path)

        result = analytics_engine.compute_dpd_flow(df_curr, df_prev)
        result['previous_date'] = prev_date
        return jsonify(result)

    except Exception as e:
        logging.error(f"Analytics DPD flow error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Fresh Slippage ──────────────────────────────────────────────

@analytics_bp.route('/api/slippage')
def api_slippage():
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date parameter required'}), 400
    try:
        cached_dates = instant_cache.list_cached_dates()
        all_dates = sorted(d['date_iso'] for d in cached_dates)

        prev_date = analytics_engine._find_previous_date(date_str, all_dates)
        if not prev_date:
            return jsonify({
                'slipped': 0, 'total_current_prev': 0, 'rate': 0,
                'previous_date': None,
                'message': 'No earlier date available for comparison',
            })

        par_curr_path = config.INSTANT_HISTORY_DIR / date_str / 'par.parquet'
        par_prev_path = config.INSTANT_HISTORY_DIR / prev_date / 'par.parquet'

        if not par_curr_path.exists() or not par_prev_path.exists():
            return jsonify({'error': 'PAR parquet missing for one or both dates'}), 404

        try:
            df_curr = pd.read_parquet(par_curr_path, columns=['AccountID', 'DPD Days', 'LoanStatus'])
            df_prev = pd.read_parquet(par_prev_path, columns=['AccountID', 'DPD Days', 'LoanStatus'])
        except Exception:
            df_curr = pd.read_parquet(par_curr_path)
            df_prev = pd.read_parquet(par_prev_path)

        result = analytics_engine.compute_fresh_slippage(df_curr, df_prev)
        result['previous_date'] = prev_date
        result['current_date'] = date_str
        return jsonify(result)

    except Exception as e:
        logging.error(f"Analytics slippage error: {e}")
        return jsonify({'error': str(e)}), 500


# ── API: Heatmap ──────────────────────────────────────────────────────

@analytics_bp.route('/api/heatmap')
def api_heatmap():
    level = request.args.get('level', 'Region')
    try:
        cached_dates = instant_cache.list_cached_dates()
        date_list = [d['date_iso'] for d in cached_dates if d.get('has_report')]
        if not date_list:
            return jsonify({'entities': [], 'dates': [], 'values': [], 'averages': []})

        date_reports = instant_cache.load_multi_date_reports(date_list)
        heatmap = analytics_engine.compute_heatmap(date_reports, level=level)
        return jsonify(heatmap)

    except Exception as e:
        logging.error(f"Analytics heatmap error: {e}")
        return jsonify({'error': str(e)}), 500
