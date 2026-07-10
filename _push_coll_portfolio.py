"""Force re-push (refresh) of collection 06-16/17/18 and portfolio FY25-26 pg_migration
months (2025-04/05/06) to AWS. Reads fresh local rows, remaps ids ABOVE the AWS max so
there are no collisions, emits delete-scope + batched multi-row INSERTs in one transaction.
Output: _push_coll_portfolio.sql"""
import pymysql

PWFILE = r"C:/Users/nlpl it/Desktop/growwithme-local/database/.env"
PW = next(l.split('=',1)[1].strip() for l in open(PWFILE, encoding='utf-8') if l.upper().startswith('MYSQL_PASSWORD='))

COLL_DATES = ['2026-06-16', '2026-06-17', '2026-06-18']
COLL_BASE  = 205830                       # AWS collection_period max -> new ids 205831+
PORT_MONTHS = ['2025-04-01', '2025-05-01', '2025-06-01']
PORT_BASE  = 4035                          # AWS portfolio_period max -> new ids 4036+

conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password=PW,
                       database='Growwithme_NEWDB', charset='utf8mb4')
cur = conn.cursor(pymysql.cursors.DictCursor)

def sv(v):
    if v is None: return 'NULL'
    if isinstance(v, (int, float)): return str(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"

def emit(out, table, cols, rows, B=500):
    for i in range(0, len(rows), B):
        chunk = rows[i:i+B]
        vals = ",".join("(" + ",".join(sv(r[c]) for c in cols) + ")" for r in chunk)
        out.append(f"INSERT INTO {table} ({','.join(cols)}) VALUES {vals};")

out = ["START TRANSACTION;"]

# ---------------- COLLECTION ----------------
dq = ",".join(["%s"]*len(COLL_DATES))
cur.execute(f"SELECT * FROM collection_period WHERE grain_id=2 AND period_date IN ({dq}) ORDER BY period_id", COLL_DATES)
cp = cur.fetchall()
old = [r['period_id'] for r in cp]
remap = {o: COLL_BASE + i + 1 for i, o in enumerate(old)}
iq = ",".join(["%s"]*len(old))
cur.execute(f"SELECT * FROM collection_dpd WHERE period_id IN ({iq})", old); cd = cur.fetchall()
cur.execute(f"SELECT * FROM collection_npa WHERE period_id IN ({iq})", old); cn = cur.fetchall()
for r in cp: r['period_id'] = remap[r['period_id']]
for r in cd: r['period_id'] = remap[r['period_id']]
for r in cn: r['period_id'] = remap[r['period_id']]
# delete-scope on AWS (children via join, then parent)
out.append(f"DELETE x FROM collection_dpd x JOIN collection_period p ON x.period_id=p.period_id WHERE p.grain_id=2 AND p.period_date IN ({','.join(repr(d) for d in COLL_DATES)});")
out.append(f"DELETE x FROM collection_npa x JOIN collection_period p ON x.period_id=p.period_id WHERE p.grain_id=2 AND p.period_date IN ({','.join(repr(d) for d in COLL_DATES)});")
out.append(f"DELETE FROM collection_period WHERE grain_id=2 AND period_date IN ({','.join(repr(d) for d in COLL_DATES)});")
emit(out, 'collection_period', ['period_id','employee_id','product_type_id','grain_id','period_date','period_hour','npa_cases','created_at','fy_id','updated_at','created_by','updated_by'], cp)
emit(out, 'collection_dpd', ['period_id','bucket_id','demand_count','demand_amt','collection_count','collection_amt'], cd)
emit(out, 'collection_npa', ['period_id','action_id','accounts','amount'], cn)
coll_counts = (len(cp), len(cd), len(cn))

# ---------------- PORTFOLIO ----------------
mq = ",".join(["%s"]*len(PORT_MONTHS))
cur.execute(f"SELECT * FROM portfolio_period WHERE period_month IN ({mq}) AND created_by='pg_migration' ORDER BY portfolio_id", PORT_MONTHS)
pp = cur.fetchall()
pold = [r['portfolio_id'] for r in pp]
premap = {o: PORT_BASE + i + 1 for i, o in enumerate(pold)}
piq = ",".join(["%s"]*len(pold))
cur.execute(f"SELECT * FROM portfolio_pos WHERE portfolio_id IN ({piq})", pold); ppos = cur.fetchall()
for r in pp: r['portfolio_id'] = premap[r['portfolio_id']]
for r in ppos: r['portfolio_id'] = premap[r['portfolio_id']]
mlist = ",".join(repr(m) for m in PORT_MONTHS)
out.append(f"DELETE x FROM portfolio_pos x JOIN portfolio_period p ON x.portfolio_id=p.portfolio_id WHERE p.period_month IN ({mlist}) AND p.created_by='pg_migration';")
out.append(f"DELETE FROM portfolio_period WHERE period_month IN ({mlist}) AND created_by='pg_migration';")
emit(out, 'portfolio_period', ['portfolio_id','branch_id','product_type_id','period_month','created_at','updated_at','created_by','updated_by'], pp)
emit(out, 'portfolio_pos', ['portfolio_id','status_id','amount'], ppos)
port_counts = (len(pp), len(ppos))

out.append("COMMIT;")
with open(r"C:/Users/nlpl it/Desktop/nlpl_Status/_push_coll_portfolio.sql", "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")

print(f"COLLECTION: period={coll_counts[0]} dpd={coll_counts[1]} npa={coll_counts[2]} -> new ids {COLL_BASE+1}..{COLL_BASE+coll_counts[0]}")
print(f"PORTFOLIO : period={port_counts[0]} pos={port_counts[1]} -> new ids {PORT_BASE+1}..{PORT_BASE+port_counts[0]}")
print("wrote _push_coll_portfolio.sql")
cur.close(); conn.close()
