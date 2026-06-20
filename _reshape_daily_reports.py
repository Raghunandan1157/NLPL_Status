"""Reshape source daily_reports -> growwithme daily_plan_* tables for the dates the
dashboard is missing (2026-06-17/18/19, incl. today). LOCAL only, idempotent. No AWS."""
import pymysql, re
def norm(s): return re.sub(r'\s*\(.*?\)', '', s or '').upper().strip()
PWFILE = r"C:/Users/nlpl it/Desktop/growwithme-local/database/.env"
PW = next(l.split('=',1)[1].strip() for l in open(PWFILE, encoding='utf-8') if l.upper().startswith('MYSQL_PASSWORD='))
conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password=PW,
                       database='Growwithme_NEWDB', charset='utf8mb4', autocommit=False)
cur = conn.cursor(pymysql.cursors.DictCursor)
cur.execute("SELECT NOW() n"); NOW = cur.fetchone()['n']
cur.execute("SELECT branch_id, branch_name FROM branch")
branch_map = {norm(r['branch_name']): r['branch_id'] for r in cur.fetchall()}

DATES = ['2026-06-17', '2026-06-18', '2026-06-19']
ph = ",".join(["%s"]*len(DATES))

# idempotent: clear prior migration rows for these dates
cur.execute(f"SELECT period_id FROM daily_plan_period WHERE plan_date IN ({ph}) AND created_by='pg_migration'", DATES)
old = [r['period_id'] for r in cur.fetchall()]
if old:
    iph = ",".join(["%s"]*len(old))
    for t in ['daily_plan_ftod','daily_plan_dpd','daily_plan_npa','daily_plan_fy_non_start','daily_plan_kyc','daily_plan_disb']:
        cur.execute(f"DELETE FROM {t} WHERE period_id IN ({iph})", old)
    cur.execute(f"DELETE FROM daily_plan_period WHERE period_id IN ({iph})", old)

cur.execute("SELECT COALESCE(MAX(period_id),0) m FROM daily_plan_period"); pid = cur.fetchone()['m']
cols = ("branch_name,date,ftod_actual,ftod_plan,dpd_1_30_actual,dpd_1_30_plan,dpd_31_60_actual,dpd_31_60_plan,"
        "dpd_61_90_actual,dpd_61_90_plan,npa_activation,npa_closure,fy_non_start_acc,fy_non_start_plan,"
        "disb_igl_acc,disb_igl_amt,disb_fig_acc,disb_fig_amt,disb_il_acc,disb_il_amt,kyc_igl,kyc_fig,kyc_il")
cur.execute(f"SELECT {cols} FROM imp_pg_daily_reports WHERE date IN ({ph})", DATES)
rows = cur.fetchall()

period_v, ftod_v, dpd_v, npa_v, fyns_v, kyc_v, disb_v = [], [], [], [], [], [], []
unres = 0
DPD = [(2,'dpd_1_30'),(3,'dpd_31_60'),(6,'dpd_61_90')]   # bucket_id, source prefix
KYC = [(1,'kyc_igl'),(2,'kyc_fig'),(3,'kyc_il')]
DISB = [(1,'disb_igl'),(2,'disb_fig'),(3,'disb_il')]
def n(x): return x if x is not None else 0
for r in rows:
    bid = branch_map.get(norm(r['branch_name']))
    if bid is None: unres += 1; continue
    pid += 1
    period_v.append((pid, bid, r['date'], 1, None, NOW, NOW, 'pg_migration', None))
    ftod_v.append((pid, n(r['ftod_actual']), n(r['ftod_plan'])))
    for bk, pre in DPD:
        dpd_v.append((pid, bk, n(r[pre+'_actual']), n(r[pre+'_plan'])))
    npa_v.append((pid, 1, n(r['npa_activation'])))
    npa_v.append((pid, 2, n(r['npa_closure'])))
    fyns_v.append((pid, n(r['fy_non_start_acc']), n(r['fy_non_start_plan'])))
    for ptid, col in KYC:
        kyc_v.append((pid, ptid, n(r[col])))
    for ptid, pre in DISB:
        disb_v.append((pid, ptid, n(r[pre+'_acc']), n(r[pre+'_amt'])))

cur.executemany("INSERT INTO daily_plan_period (period_id,branch_id,plan_date,submission_type_id,dm_employee_id,created_at,updated_at,created_by,updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", period_v)
cur.executemany("INSERT INTO daily_plan_ftod (period_id,actual_count,plan_count) VALUES (%s,%s,%s)", ftod_v)
cur.executemany("INSERT INTO daily_plan_dpd (period_id,bucket_id,actual_count,plan_count) VALUES (%s,%s,%s,%s)", dpd_v)
cur.executemany("INSERT INTO daily_plan_npa (period_id,action_id,count) VALUES (%s,%s,%s)", npa_v)
cur.executemany("INSERT INTO daily_plan_fy_non_start (period_id,actual_count,plan_count) VALUES (%s,%s,%s)", fyns_v)
cur.executemany("INSERT INTO daily_plan_kyc (period_id,product_type_id,kyc_count) VALUES (%s,%s,%s)", kyc_v)
cur.executemany("INSERT INTO daily_plan_disb (period_id,product_type_id,accounts,amount) VALUES (%s,%s,%s,%s)", disb_v)
conn.commit()
print(f"daily reports added: periods={len(period_v)} ftod={len(ftod_v)} dpd={len(dpd_v)} npa={len(npa_v)} fyns={len(fyns_v)} kyc={len(kyc_v)} disb={len(disb_v)} | branch_unresolved={unres}")
cur.close(); conn.close()
