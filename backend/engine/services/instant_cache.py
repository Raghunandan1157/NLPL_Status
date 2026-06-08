"""
Instant Report - Date-Based Cache Manager
==========================================
Manages daily PAR + Collection parquet caches for the Instant Report
calendar history feature. Each successful report generation saves
cached data into data/instant-history/YYYY-MM-DD/.

Cache structure per date:
  par.parquet          - PAR DataFrame
  collection.parquet   - Collection DataFrame
  metadata.json        - Filenames, hashes, row counts, timestamps
  report.json          - Computed report (output of compute_instant_report)
"""

import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
from services.cache_manager import is_disk_critical, is_disk_pressure, ensure_cache_budget, get_dir_size_bytes
from services.column_matcher import find_column


def save_date_cache(date_str, df_par, df_collection, metadata_dict, report_data=None):
    """
    Atomically save PAR + Collection parquets + metadata + report for a date.

    Args:
        date_str: ISO date string 'YYYY-MM-DD'
        df_par: PAR DataFrame
        df_collection: Collection DataFrame
        metadata_dict: dict with filenames, hashes, row counts, etc.
        report_data: optional dict from compute_instant_report() to cache as report.json

    Uses temp folder + rename for atomicity.
    """
    history_dir = config.INSTANT_HISTORY_DIR
    final_folder = history_dir / date_str
    temp_folder = history_dir / f'.tmp_{date_str}_{int(time.time())}'

    # Check disk pressure before writing new cache entry
    if is_disk_critical():
        logging.warning(
            "Instant cache: Skipping save for %s -- disk space critically low (<2GB free). "
            "Existing caches preserved. Free disk space to resume caching.",
            date_str
        )
        return False

    try:
        temp_folder.mkdir(parents=True, exist_ok=True)

        # Write parquets
        df_par.to_parquet(temp_folder / 'par.parquet', index=False)
        df_collection.to_parquet(temp_folder / 'collection.parquet', index=False)

        # Write metadata
        meta = {
            'date': date_str,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            **metadata_dict,
        }
        with open(temp_folder / 'metadata.json', 'w') as f:
            json.dump(meta, f, indent=2)

        # Write report JSON if provided
        if report_data is not None:
            with open(temp_folder / 'report.json', 'w') as f:
                json.dump(report_data, f, separators=(',', ':'))

        # Atomic swap: remove old, rename temp to final
        if final_folder.exists():
            shutil.rmtree(final_folder)
        temp_folder.rename(final_folder)

        logging.info(f"Instant cache: Saved {date_str} "
                     f"(PAR={len(df_par)} rows, Coll={len(df_collection)} rows"
                     f"{', +report.json' if report_data else ''})")

        # Auto-cleanup old caches
        _cleanup_old_caches()

        return True

    except Exception as e:
        logging.warning(f"Instant cache: Failed to save {date_str}: {e}")
        # Clean up temp folder on failure
        if temp_folder.exists():
            shutil.rmtree(temp_folder, ignore_errors=True)
        return False


def load_date_cache(date_str):
    """
    Load cached PAR + Collection DataFrames for a date.

    Args:
        date_str: ISO date string 'YYYY-MM-DD'

    Returns:
        (df_par, df_collection, metadata) tuple

    Raises:
        FileNotFoundError: if cache doesn't exist
        ValueError: if cache is invalid/corrupt
    """
    date_folder = config.INSTANT_HISTORY_DIR / date_str

    if not date_folder.exists():
        raise FileNotFoundError(f"No cache found for {date_str}")

    is_valid, error = validate_date_cache(date_folder)
    if not is_valid:
        raise ValueError(f"Cache for {date_str} is invalid: {error}")

    par_path = date_folder / 'par.parquet'
    coll_path = date_folder / 'collection.parquet'
    meta_path = date_folder / 'metadata.json'

    df_par = pd.read_parquet(par_path)  # Full read: PAR columns vary and consumers need most columns
    df_collection = pd.read_parquet(coll_path)  # Full read: Collection columns vary and consumers need most columns

    with open(meta_path, 'r') as f:
        metadata = json.load(f)

    logging.info(f"Instant cache: Loaded {date_str} "
                 f"(PAR={len(df_par)} rows, Coll={len(df_collection)} rows)")

    return df_par, df_collection, metadata


def save_date_report(date_str, report_data):
    """
    Save (or overwrite) report.json into an existing date cache folder.
    Used to backfill report JSON for dates cached before this feature.

    Args:
        date_str: ISO date string 'YYYY-MM-DD'
        report_data: dict from compute_instant_report()

    Returns:
        True on success, False on failure.
    """
    date_folder = config.INSTANT_HISTORY_DIR / date_str
    if not date_folder.exists():
        logging.warning(f"Instant cache: Cannot save report for {date_str}, no cache folder")
        return False

    try:
        report_path = date_folder / 'report.json'
        with open(report_path, 'w') as f:
            json.dump(report_data, f, separators=(',', ':'))
        logging.info(f"Instant cache: Saved report.json for {date_str}")
        return True
    except Exception as e:
        logging.warning(f"Instant cache: Failed to save report.json for {date_str}: {e}")
        return False


def load_date_report(date_str):
    """
    Load the cached report.json for a date (fast path, no parquet loading).

    Args:
        date_str: ISO date string 'YYYY-MM-DD'

    Returns:
        report_data dict (same structure as compute_instant_report output)

    Raises:
        FileNotFoundError: if cache or report.json doesn't exist
    """
    report_path = config.INSTANT_HISTORY_DIR / date_str / 'report.json'
    if not report_path.exists():
        raise FileNotFoundError(f"No cached report for {date_str}")

    with open(report_path, 'r') as f:
        return json.load(f)


def load_multi_date_reports(date_list):
    """
    Load report.json for multiple dates at once. Skips dates without reports.

    Args:
        date_list: list of ISO date strings ['YYYY-MM-DD', ...]

    Returns:
        dict mapping date_str -> report_data for dates that have cached reports.
        Dates without report.json are omitted from the result.
    """
    results = {}
    for date_str in date_list:
        try:
            results[date_str] = load_date_report(date_str)
        except FileNotFoundError:
            logging.debug(f"Instant cache: No report.json for {date_str}, skipping")
    return results


def extract_entity_data(report_data, entity_name, entity_level='Region'):
    """
    Extract a specific entity's rows from a cached report across all sections.

    The report JSON structure is:
      { "sections": [
          { "title": "Regular Demand vs Collection",
            "tables": [
              { "level": "Region", "rows": [{"name": "EAST", "demand": 100, ...}, ...],
                "grand_total": {...} },
              { "level": "Division", ... },
              { "level": "Area", ... },
              { "level": "Branch", ... }
            ]
          },
          ...
        ],
        "metadata": { ... }
      }

    Args:
        report_data: full report dict (from load_date_report or compute_instant_report)
        entity_name: name to match (e.g. "EAST", "PATNA")
        entity_level: which level to search in ("Region", "Division", "Area", "Branch")

    Returns:
        dict mapping section_title -> entity_row_dict for sections where entity was found.
        Example: {
            "Regular Demand vs Collection": {"name": "EAST", "demand": 100, "collection": 95, ...},
            "1-30 DPD Bucket": {"name": "EAST", "demand": 50, "collection": 40, ...},
            ...
        }
    """
    entity_upper = entity_name.strip().upper()
    is_grand_total = entity_upper in ('NLPL', 'GRAND TOTAL')
    result = {}

    for section in report_data.get('sections', []):
        title = section.get('title', '')

        if is_grand_total:
            # For NLPL / Grand Total, grab grand_total from the first table
            # matching the entity_level (default Region)
            for table in section.get('tables', []):
                if table.get('level') == entity_level:
                    gt = table.get('grand_total')
                    if gt:
                        result[title] = gt
                    break
        else:
            for table in section.get('tables', []):
                if table.get('level') != entity_level:
                    continue
                for row in table.get('rows', []):
                    if row.get('name', '').strip().upper() == entity_upper:
                        result[title] = row
                        break

    return result


def get_hierarchy_from_parquet(date_str):
    """
    Read the PAR parquet for a given date and extract the Region/Division/Area/Branch
    hierarchy using fuzzy column matching.

    Args:
        date_str: ISO date string 'YYYY-MM-DD'

    Returns:
        dict with keys:
            regions: sorted list of unique region names
            region_to_divisions: { region: sorted list of divisions }
            division_to_areas: { division: sorted list of areas }
            area_to_branches: { area: sorted list of branches }

    Raises:
        FileNotFoundError: if the PAR parquet does not exist for the date
    """
    par_path = config.INSTANT_HISTORY_DIR / date_str / 'par.parquet'
    if not par_path.exists():
        raise FileNotFoundError(f"No PAR parquet for {date_str}")

    try:
        df = pd.read_parquet(par_path, columns=['AccountID', 'Emp ID', 'Emp Name', 'BranchName', 'Region', 'Division', 'Area', 'AreaName'])
    except Exception:
        df = pd.read_parquet(par_path)

    region_col = find_column(df, 'Region', 'RegionName', 'Region Name')
    division_col = find_column(df, 'Division', 'DivisionName', 'Division Name')
    area_col = find_column(df, 'Area', 'AreaName', 'Area Name', 'District', 'DistrictName', 'District Name')
    branch_col = find_column(df, 'BranchName', 'Branch Name', 'Branch', 'Branchname')

    regions = []
    region_to_divisions = {}
    division_to_areas = {}
    area_to_branches = {}

    if region_col:
        regions = sorted(df[region_col].dropna().astype(str).str.strip().unique())

    if region_col and division_col:
        for region in regions:
            mask = df[region_col].astype(str).str.strip() == region
            divisions = sorted(
                df.loc[mask, division_col].dropna().astype(str).str.strip().unique()
            )
            region_to_divisions[region] = divisions

    if division_col and area_col:
        all_divisions = sorted(df[division_col].dropna().astype(str).str.strip().unique())
        for division in all_divisions:
            mask = df[division_col].astype(str).str.strip() == division
            areas = sorted(
                df.loc[mask, area_col].dropna().astype(str).str.strip().unique()
            )
            division_to_areas[division] = areas

    if area_col and branch_col:
        all_areas = sorted(df[area_col].dropna().astype(str).str.strip().unique())
        for area in all_areas:
            mask = df[area_col].astype(str).str.strip() == area
            branches = sorted(
                df.loc[mask, branch_col].dropna().astype(str).str.strip().unique()
            )
            area_to_branches[area] = branches

    return {
        'regions': regions,
        'region_to_divisions': region_to_divisions,
        'division_to_areas': division_to_areas,
        'area_to_branches': area_to_branches,
    }


def get_hierarchy_from_report(report_data):
    """
    Extract flat lists of entity names from report.json by level.
    Less accurate than parquet (no parent-child mapping), but works without
    loading parquet data.

    Args:
        report_data: full report dict (from load_date_report)

    Returns:
        dict with keys:
            regions: sorted list of region names
            divisions: sorted list of division names
            areas: sorted list of area names
            branches: sorted list of branch names
    """
    regions = set()
    divisions = set()
    areas = set()
    branches = set()

    # Use the first section to gather entity names (all sections should have
    # the same entity names since they come from the same data)
    for section in report_data.get('sections', []):
        for table in section.get('tables', []):
            level = table.get('level', '')
            names = {
                row.get('name', '').strip()
                for row in table.get('rows', [])
                if row.get('name', '').strip()
            }
            if level == 'Region':
                regions.update(names)
            elif level == 'Division':
                divisions.update(names)
            elif level == 'Area':
                areas.update(names)
            elif level == 'Branch':
                branches.update(names)

    return {
        'regions': sorted(regions),
        'divisions': sorted(divisions),
        'areas': sorted(areas),
        'branches': sorted(branches),
    }


def list_cached_dates():
    """
    List all dates with valid cached data.

    Returns:
        List of dicts with date info and metadata summary.
    """
    history_dir = config.INSTANT_HISTORY_DIR
    if not history_dir.exists():
        return []

    dates = []
    for date_folder in sorted(history_dir.iterdir()):
        if not date_folder.is_dir():
            continue

        # Skip temp folders
        if date_folder.name.startswith('.tmp_'):
            # Clean up stale temp folders (older than 5 minutes)
            try:
                age = time.time() - date_folder.stat().st_mtime
                if age > 300:
                    shutil.rmtree(date_folder, ignore_errors=True)
            except Exception:
                pass
            continue

        # Validate folder name is a date
        folder_name = date_folder.name
        try:
            datetime.strptime(folder_name, '%Y-%m-%d')
        except ValueError:
            continue

        # Check required files exist
        is_valid, _ = validate_date_cache(date_folder)
        if not is_valid:
            continue

        # Convert to display format DD-MM-YYYY
        date_display = f"{folder_name[8:10]}-{folder_name[5:7]}-{folder_name[0:4]}"

        entry = {
            'date_iso': folder_name,
            'date_display': date_display,
            'has_report': (date_folder / 'report.json').exists(),
        }

        # Load metadata if available
        meta_path = date_folder / 'metadata.json'
        if meta_path.exists():
            try:
                with open(meta_path, 'r') as f:
                    meta = json.load(f)
                entry['par_filename'] = meta.get('par_original_filename', '')
                entry['collection_filename'] = meta.get('collection_original_filename', '')
                entry['par_rows'] = meta.get('par_rows', 0)
                entry['collection_rows'] = meta.get('collection_rows', 0)
                entry['generated_at'] = meta.get('updated_at') or meta.get('created_at', '')
            except Exception:
                pass

        dates.append(entry)

    return dates


def validate_date_cache(date_folder):
    """
    Validate that a date cache folder has all required files and they're not corrupt.

    Returns:
        (is_valid: bool, error_msg: str or None)
    """
    par_path = date_folder / 'par.parquet'
    coll_path = date_folder / 'collection.parquet'
    meta_path = date_folder / 'metadata.json'

    # Check files exist
    if not par_path.exists() or not coll_path.exists():
        return False, "Missing parquet files"

    if not meta_path.exists():
        return False, "Missing metadata.json"

    # Check file sizes > 0
    if par_path.stat().st_size == 0:
        return False, "PAR parquet is empty"
    if coll_path.stat().st_size == 0:
        return False, "Collection parquet is empty"

    # Validate metadata JSON
    try:
        with open(meta_path, 'r') as f:
            meta = json.load(f)
        if 'date' not in meta:
            return False, "Metadata missing date field"
    except (json.JSONDecodeError, KeyError):
        return False, "Metadata corrupted"

    return True, None


def _cleanup_old_caches():
    """Remove old cache folders based on count, size, and disk pressure."""
    try:
        history_dir = config.INSTANT_HISTORY_DIR
        max_days = config.INSTANT_CACHE_MAX_DAYS

        if not history_dir.exists():
            return

        # Get all valid date folders sorted by name (oldest first)
        date_folders = []
        for folder in sorted(history_dir.iterdir()):
            if folder.is_dir() and not folder.name.startswith('.'):
                try:
                    datetime.strptime(folder.name, '%Y-%m-%d')
                    date_folders.append(folder)
                except ValueError:
                    continue

        # Phase 1: Count-based eviction (preserve existing behavior)
        while len(date_folders) > max_days:
            oldest = date_folders.pop(0)
            shutil.rmtree(oldest, ignore_errors=True)
            logging.info(f"Instant cache: Auto-removed old cache {oldest.name} (count limit)")

        # Phase 2: Size-based eviction (new -- CACHE-03/CACHE-04)
        max_size_bytes = config.INSTANT_HISTORY_MAX_SIZE_MB * 1024 * 1024
        while date_folders and get_dir_size_bytes(history_dir) > max_size_bytes:
            oldest = date_folders.pop(0)
            shutil.rmtree(oldest, ignore_errors=True)
            logging.info(f"Instant cache: Auto-removed old cache {oldest.name} (size limit)")

        # Phase 3: Disk pressure eviction (new -- CACHE-04)
        while date_folders and is_disk_pressure():
            oldest = date_folders.pop(0)
            shutil.rmtree(oldest, ignore_errors=True)
            logging.info(f"Instant cache: Auto-removed old cache {oldest.name} (disk pressure)")

    except Exception as e:
        logging.warning(f"Instant cache cleanup error: {e}")
