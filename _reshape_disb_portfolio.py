"""Reshape source disbursement (monthly) + portfolio (branch POS) into the
dashboard's normalized tables, ADDING the historical months the dashboard lacks.
LOCAL only, idempotent for the added periods. No AWS."""
import pymysql

PWFILE = r"C:/Users/nlpl it/Desktop/growwithme-local/database/.env"
PW = next(l.split('=',1)[1].strip() for l in open(PWFILE, encoding='utf-8') if l.upper().startswith('MYSQL_PASSWORD='))
conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password=PW,
                       database='Growwithme_NEWDB', charset='utf8mb4', autocommit=False)
cur = conn.cursor(pymysql.cursors.DictCursor)

cur.execute("SELECT NOW() AS n"); NOW = cur.fetchone()['n']
def mp(sql):
    cur.execute(sql); return cur.fetchall()
branch_map  = {r['branch_name']: r['branch_id'] for r in mp("SELECT branch_id, branch_name FROM branch")}
emp_map     = {r['employee_code']: r['employee_id'] for r in mp("SELECT employee_id, employee_code FROM employee")}
prod_map    = {r['product_type_name']: r['product_type_id'] for r in mp("SELECT product_type_id, product_type_name FROM product_type")}

MON = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
def conv_month(s):            # 'Apr-25' -> '2025-04-01'
    mon, yy = s.split('-')
    return f"20{yy}-{MON[mon]:02d}-01"

# ---------- DISBURSEMENT (monthly) ----------
existing_disb = {str(r['m']) for r in mp("SELECT DISTINCT db_month m FROM disbursement")}
cur.execute("SELECT COALESCE(MAX(id),0) AS m FROM disbursement"); did = cur.fetchone()['m']
rows = mp("SELECT db_month, branch_name, emp_id, product_name, disb_count, disb_amount FROM imp_pg_disbursement")
disb_vals = []; skipped_disb = 0; unres_emp = 0
add_months = set()
for r in rows:
    dm = conv_month(r['db_month'])
    if dm in existing_disb:          # overlap (e.g. 2026-03) — keep dashboard's, skip source
        skipped_disb += 1; continue
    bid = branch_map.get(r['branch_name'])
    eid = emp_map.get(r['emp_id'])
    pid = prod_map.get(r['product_name'])
    if eid is None: unres_emp += 1
    did += 1; add_months.add(dm)
    disb_vals.append((did, bid, eid, pid, dm, r['disb_count'], r['disb_amount'], NOW, NOW, 'pg_migration', None))
# idempotent: clear any prior migration rows for the months we're adding
if add_months:
    iph = ",".join(["%s"]*len(add_months))
    cur.execute(f"DELETE FROM disbursement WHERE db_month IN ({iph}) AND created_by='pg_migration'", list(add_months))
cur.executemany("""INSERT INTO disbursement
  (id, branch_id, employee_id, product_type_id, db_month, disb_count, disb_amount, created_at, updated_at, created_by, updated_by)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", disb_vals)
print(f"DISBURSEMENT: added {len(disb_vals)} rows across {len(add_months)} months {sorted(add_months)} | skipped(overlap)={skipped_disb} | emp_unresolved={unres_emp}")

# ---------- PORTFOLIO (branch POS) ----------
# Source months missing from dashboard: Apr/May/Jun 2025 (month_id 13/8/9 in FY25-26).
NEW_PORT = {13: '2025-04-01', 8: '2025-05-01', 9: '2025-06-01'}
POS_COLS = [(1,'regular_pos'),(2,'sma0_pos'),(3,'sma1_pos'),(4,'pnpa_pos'),(5,'npa_pos'),(6,'total_pos')]
cur.execute("SELECT COALESCE(MAX(portfolio_id),0) AS m FROM portfolio_period"); pid0 = cur.fetchone()['m']
# idempotent: clear any prior migration rows for these months
months_list = list(NEW_PORT.values())
mph = ",".join(["%s"]*len(months_list))
cur.execute(f"SELECT portfolio_id FROM portfolio_period WHERE period_month IN ({mph}) AND created_by='pg_migration'", months_list)
old = [r['portfolio_id'] for r in cur.fetchall()]
if old:
    oph = ",".join(["%s"]*len(old))
    cur.execute(f"DELETE FROM portfolio_pos WHERE portfolio_id IN ({oph})", old)
    cur.execute(f"DELETE FROM portfolio_period WHERE portfolio_id IN ({oph})", old)

per_vals=[]; pos_vals=[]; pp=pid0; unres_b=0
for mid, pmonth in NEW_PORT.items():
    src = mp(f"SELECT branch_name, product_name, regular_pos, sma0_pos, sma1_pos, pnpa_pos, npa_pos, total_pos FROM imp_pmw_branch_pos WHERE month_id={mid}")
    for r in src:
        bid = branch_map.get(r['branch_name'])
        ptid = prod_map.get(r['product_name'])
        if bid is None: unres_b += 1; continue
        pp += 1
        per_vals.append((pp, bid, ptid, pmonth, NOW, NOW, 'pg_migration', None))
        for sid, col in POS_COLS:
            pos_vals.append((pp, sid, r[col]))
cur.executemany("""INSERT INTO portfolio_period
  (portfolio_id, branch_id, product_type_id, period_month, created_at, updated_at, created_by, updated_by)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", per_vals)
cur.executemany("INSERT INTO portfolio_pos (portfolio_id, status_id, amount) VALUES (%s,%s,%s)", pos_vals)
print(f"PORTFOLIO: added {len(per_vals)} periods + {len(pos_vals)} pos rows for {months_list} | branch_unresolved={unres_b}")

conn.commit(); cur.close(); conn.close()
