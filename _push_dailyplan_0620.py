"""Generate an idempotent SQL file to push ONLY 06-20 (today) daily_plan rows to AWS.
Reads the reshaped 06-20 rows from LOCAL Growwithme_NEWDB, remaps their period_ids to a
fresh range above the AWS current max (no collisions), and emits delete-scope + INSERTs.
Output: _push_0620.sql  (apply on AWS via: sudo mysql Growwithme_NEWDB < file)."""
import pymysql

PWFILE = r"C:/Users/nlpl it/Desktop/growwithme-local/database/.env"
PW = next(l.split('=',1)[1].strip() for l in open(PWFILE, encoding='utf-8') if l.upper().startswith('MYSQL_PASSWORD='))
AWS_MAX = 7685          # current MAX(period_id) on AWS daily_plan_period (verified)
NEW_BASE = AWS_MAX + 1  # first new id
DATE = '2026-06-20'

conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password=PW,
                       database='Growwithme_NEWDB', charset='utf8mb4')
cur = conn.cursor(pymysql.cursors.DictCursor)

cur.execute("SELECT * FROM daily_plan_period WHERE plan_date=%s AND created_by='pg_migration' ORDER BY period_id", (DATE,))
periods = cur.fetchall()
old_ids = [r['period_id'] for r in periods]
remap = {oid: NEW_BASE + i for i, oid in enumerate(old_ids)}  # compact new ids above AWS max

def sv(v):
    if v is None: return 'NULL'
    if isinstance(v, (int, float)): return str(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"

CHILDREN = {
    'daily_plan_ftod':         ['period_id','actual_count','plan_count'],
    'daily_plan_dpd':          ['period_id','bucket_id','actual_count','plan_count'],
    'daily_plan_npa':          ['period_id','action_id','count'],
    'daily_plan_fy_non_start': ['period_id','actual_count','plan_count'],
    'daily_plan_kyc':          ['period_id','product_type_id','kyc_count'],
    'daily_plan_disb':         ['period_id','product_type_id','accounts','amount'],
}
PCOLS = ['period_id','branch_id','plan_date','submission_type_id','dm_employee_id',
         'created_at','updated_at','created_by','updated_by']

out = []
out.append("START TRANSACTION;")
# idempotent delete-scope: remove any prior 06-20 pg_migration rows (children via join, then parent)
for t in CHILDREN:
    out.append(f"DELETE c FROM {t} c JOIN daily_plan_period p ON c.period_id=p.period_id "
               f"WHERE p.plan_date='{DATE}' AND p.created_by='pg_migration';")
out.append(f"DELETE FROM daily_plan_period WHERE plan_date='{DATE}' AND created_by='pg_migration';")

# parent inserts (remapped ids)
for r in periods:
    r = dict(r); r['period_id'] = remap[r['period_id']]
    vals = ", ".join(sv(r[c]) for c in PCOLS)
    out.append(f"INSERT INTO daily_plan_period ({','.join(PCOLS)}) VALUES ({vals});")
# children inserts (remapped ids)
insert_n = {'daily_plan_period': len(periods)}
for t, cols in CHILDREN.items():
    ph = ",".join(["%s"]*len(old_ids))
    cur.execute(f"SELECT {','.join(cols)} FROM {t} WHERE period_id IN ({ph}) ORDER BY period_id", old_ids)
    rows = cur.fetchall(); insert_n[t] = len(rows)
    for r in rows:
        r = dict(r); r['period_id'] = remap[r['period_id']]
        vals = ", ".join(sv(r[c]) for c in cols)
        out.append(f"INSERT INTO {t} ({','.join(cols)}) VALUES ({vals});")
out.append("COMMIT;")

with open(r"C:/Users/nlpl it/Desktop/nlpl_Status/_push_0620.sql", "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")

print(f"period rows: {len(periods)} | new id range: {NEW_BASE}..{NEW_BASE+len(periods)-1}")
print("insert counts:", insert_n)
print("wrote _push_0620.sql")
cur.close(); conn.close()
