"""
Shared DuckDB Manager - adapted from EOD/db_manager.py
Handles Demand_Master and Last_Month_PAR tables with Parquet caching.

Provides a singleton via get_db_manager() so all blueprints share one
DuckDB connection (DuckDB enforces single-writer; multiple connections
to the same file cause locking errors, especially on Windows).
"""
import atexit
import os
import threading
import duckdb
import logging
from pathlib import Path
import pandas as pd

import config
from services.hardware_profile import DUCKDB_MEMORY_MB, DUCKDB_THREADS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ---------------------------------------------------------------------------
# Singleton state
# ---------------------------------------------------------------------------
_instance = None
_lock = threading.Lock()


def get_db_manager():
    """
    Return the shared DBManager singleton.

    Thread-safe: uses a lock to ensure only one instance is ever created.
    Returns None if initialisation fails (logged as error).
    """
    global _instance
    if _instance is not None:
        return _instance

    with _lock:
        # Double-checked locking
        if _instance is not None:
            return _instance
        try:
            _instance = DBManager(config.DUCKDB_PATH)
            atexit.register(_instance.close)
            logging.info("Shared DBManager singleton initialized")
        except Exception as e:
            logging.error(f"Failed to initialize shared DBManager: {e}")
    return _instance


# ---------------------------------------------------------------------------
# DBManager class
# ---------------------------------------------------------------------------

class DBManager:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.db_path))

        # DuckDB memory and threads auto-tuned from hardware profile
        self.con.execute(f"SET memory_limit = '{DUCKDB_MEMORY_MB}MB'")
        self.con.execute(f"SET threads = {DUCKDB_THREADS}")

        self._init_schema()

    def _init_schema(self):
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS Demand_Master (
                AccountID VARCHAR,
                CustomerName VARCHAR,
                Regular_Demand DOUBLE
            )
        """)
        logging.info("Database schema initialized.")

    def ingest_demand_master(self, file_path):
        try:
            logging.info(f"Ingesting Demand Master from {file_path}")
            file_path = Path(file_path)
            parquet_path = self.db_path.parent / "demand_master_cache.parquet"

            self.con.execute("DROP TABLE IF EXISTS Demand_Master")

            if parquet_path.exists() and parquet_path.stat().st_mtime > file_path.stat().st_mtime:
                logging.info(f"Using Parquet cache: {parquet_path.name}")
                self.con.execute("CREATE TABLE Demand_Master AS SELECT * FROM read_parquet(?)", [str(parquet_path)])
            else:
                logging.info("Reading from Excel (first time or file changed)...")
                try:
                    df = pd.read_excel(file_path, sheet_name=0, engine='calamine')
                except Exception as e:
                    logging.warning(f"calamine engine failed for demand ingestion, falling back to openpyxl: {e}")
                    df = pd.read_excel(file_path, sheet_name=0)
                for col in df.columns:
                    if df[col].dtype == 'object':
                        df[col] = df[col].astype(str).replace('nan', '').replace('None', '')
                logging.info(f"Creating Parquet cache: {parquet_path.name}")
                df.to_parquet(parquet_path, index=False)
                self.con.execute("CREATE TABLE Demand_Master AS SELECT * FROM df")

            row_count = self.con.execute("SELECT count(*) FROM Demand_Master").fetchone()[0]
            logging.info(f"Successfully ingested {row_count} rows into Demand_Master.")
            return True, f"Ingested {row_count} rows."
        except Exception as e:
            logging.error(f"Error ingesting demand file: {e}")
            return False, str(e)

    def ingest_last_month_par(self, file_path):
        try:
            logging.info(f"Ingesting Last Month PAR from {file_path}")
            file_path = Path(file_path)
            parquet_path = self.db_path.parent / "last_month_par_cache.parquet"

            self.con.execute("DROP TABLE IF EXISTS Last_Month_PAR")

            if parquet_path.exists() and parquet_path.stat().st_mtime > file_path.stat().st_mtime:
                logging.info(f"Using Parquet cache: {parquet_path.name}")
                self.con.execute("CREATE TABLE Last_Month_PAR AS SELECT * FROM read_parquet(?)", [str(parquet_path)])
            else:
                logging.info("Reading from Excel (first time or file changed)...")
                # Use smart_read_excel so the account-level data sheet ('Sheet1')
                # is read — NOT a pivot/summary sheet that may sit on sheet 0.
                # Reading sheet 0 here previously cached a 130-row branch summary,
                # which blanked the DPD buckets in the EOD Report.
                from services.excel_reader import smart_read_excel
                needed_cols = ['AccountID', 'DPD Days', 'LoanStatus']
                try:
                    df = smart_read_excel(file_path, usecols=needed_cols)
                except Exception as e:
                    logging.warning(f"Last Month PAR usecols read failed ({e}); reading full sheet")
                    df = smart_read_excel(file_path)
                    df = df[[c for c in needed_cols if c in df.columns]]

                if 'AccountID' in df.columns:
                    df['AccountID'] = df['AccountID'].astype(str).str.strip()
                    df = df[~df['AccountID'].str.contains('\ufffd|nan|None', na=True, case=False)]

                logging.info(f"Creating Parquet cache: {parquet_path.name}")
                df.to_parquet(parquet_path, index=False)
                self.con.execute("CREATE TABLE Last_Month_PAR AS SELECT * FROM df")

            row_count = self.con.execute("SELECT count(*) FROM Last_Month_PAR").fetchone()[0]
            logging.info(f"Successfully ingested {row_count} rows into Last_Month_PAR.")
            return True, f"Ingested {row_count} rows."
        except Exception as e:
            logging.error(f"Error ingesting Last Month PAR: {e}")
            return False, str(e)

    def get_connection(self):
        return self.con

    def close(self):
        try:
            self.con.close()
            logging.info("DBManager connection closed.")
        except Exception:
            pass
