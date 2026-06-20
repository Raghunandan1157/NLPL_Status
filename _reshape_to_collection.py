"""Reshape fresh imp_pg_daily_performance (flat) -> collection_period + collection_dpd
(+ collection_npa) for the dates missing from the normalized tables, so the growwithme
dashboards (Demand/Collection/FTOD) show current data. LOCAL only. Idempotent for the
target dates. No AWS."""
import pymysql

PWFILE = r"C:/Users/nlpl it/Desktop/growwithme-local/database/.env"
PW = next(l.split('=',1)[1].strip() for l in open(PWFILE, encoding='utf-8') if l.upper().startswith('MYSQL_PASSWORD='))

DATES = ['2026-06-16', '2026-06-17', '2026-06-18']  # the days collection_period is missing
GRAIN = 2     # daily
FY_ID = 3     # FY26-27
# bucket_id -> (demand_count_col, demand_amt_col, collection_count_col, collection_amt_col)
BUCKETS = {
    1: ('regular_demand','regular_demand_amt','regular_collection','regular_collection_amt'),
    2: ('demand_1_30','demand_1_30_amt','collection_1_30','collection_1_30_amt'),
    3: ('demand_31_60','demand_31_60_amt','collection_31_60','collection_31_60_amt'),
    4: ('pnpa_demand','pnpa_demand_amt','pnpa_collection','pnpa_collection_amt'),
    5: ('on_date_demand','on_date_demand_amt','on_date_collection','on_date_collection_amt'),
}

conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password=PW,
                       database='Growwithme_NEWDB', charset='utf8mb4', autocommit=False)
cur = conn.cursor(pymysql.cursors.DictCursor)

# employee_code -> employee_id map (avoids the cross-collation join)
cur.execute("SELECT employee_id, employee_code FROM employee")
emp_map = {r['employee_code']: r['employee_id'] for r in cur.fetchall()}

cur.execute("SELECT NOW() AS now"); NOW = cur.fetchone()['now']

# idempotent: clear any existing rows for these dates (children first)
ph = ",".join(["%s"]*len(DATES))
cur.execute(f"SELECT period_id FROM collection_period WHERE grain_id=%s AND period_date IN ({ph})", [GRAIN]+DATES)
old_ids = [r['period_id'] for r in cur.fetchall()]
if old_ids:
    iph = ",".join(["%s"]*len(old_ids))
    cur.execute(f"DELETE FROM collection_dpd WHERE period_id IN ({iph})", old_ids)
    cur.execute(f"DELETE FROM collection_npa WHERE period_id IN ({iph})", old_ids)
    cur.execute(f"DELETE FROM collection_period WHERE period_id IN ({iph})", old_ids)
    print(f"cleared {len(old_ids)} pre-existing periods for these dates")

cur.execute("SELECT COALESCE(MAX(period_id),0) AS m FROM collection_period")
pid = cur.fetchone()['m']

# read the fresh flat rows
cols = ['report_date','emp_id','product_type_id','npa_cases',
        'npa_act_acc','npa_act_amt','npa_clo_acc','npa_clo_amt'] + \
       sorted({c for t in BUCKETS.values() for c in t})
cur.execute(f"SELECT {','.join(cols)} FROM imp_pg_daily_performance WHERE report_date IN ({ph})", DATES)
rows = cur.fetchall()

period_vals, dpd_vals, npa_vals = [], [], []
unresolved = 0
for r in rows:
    eid = emp_map.get(r['emp_id'])
    if eid is None:
        unresolved += 1; continue
    pid += 1
    period_vals.append((pid, eid, r['product_type_id'], GRAIN, r['report_date'], None,
                        r['npa_cases'], NOW, FY_ID, NOW, None, None))
    for b, (dc, da, cc, ca) in BUCKETS.items():
        dpd_vals.append((pid, b, r[dc], r[da], r[cc], r[ca]))
    if (r['npa_act_acc'] or r['npa_act_amt']):
        npa_vals.append((pid, 1, r['npa_act_acc'] or 0, r['npa_act_amt'] or 0))
    if (r['npa_clo_acc'] or r['npa_clo_amt']):
        npa_vals.append((pid, 2, r['npa_clo_acc'] or 0, r['npa_clo_amt'] or 0))

cur.executemany("""INSERT INTO collection_period
  (period_id, employee_id, product_type_id, grain_id, period_date, period_hour, npa_cases, created_at, fy_id, updated_at, created_by, updated_by)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", period_vals)
cur.executemany("""INSERT INTO collection_dpd
  (period_id, bucket_id, demand_count, demand_amt, collection_count, collection_amt)
  VALUES (%s,%s,%s,%s,%s,%s)""", dpd_vals)
if npa_vals:
    cur.executemany("""INSERT INTO collection_npa (period_id, action_id, accounts, amount)
      VALUES (%s,%s,%s,%s)""", npa_vals)
conn.commit()

print(f"inserted: periods={len(period_vals)}  dpd={len(dpd_vals)}  npa={len(npa_vals)}  unresolved_emp={unresolved}")
cur.close(); conn.close()
