"""Push the newly-available 06-19 collection (period+dpd+npa) to AWS. Remaps ids above
AWS collection_period max so no collisions; idempotent delete-scope on the date. One txn."""
import pymysql
PWFILE = r"C:/Users/nlpl it/Desktop/growwithme-local/database/.env"
PW = next(l.split('=',1)[1].strip() for l in open(PWFILE, encoding='utf-8') if l.upper().startswith('MYSQL_PASSWORD='))
DATE = '2026-06-19'
BASE = 210342   # AWS collection_period max -> new ids 210343+

conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password=PW,
                       database='Growwithme_NEWDB', charset='utf8mb4')
cur = conn.cursor(pymysql.cursors.DictCursor)
def sv(v):
    if v is None: return 'NULL'
    if isinstance(v, (int, float)): return str(v)
    return "'" + str(v).replace("\\","\\\\").replace("'","\\'") + "'"
def emit(out, table, cols, rows, B=500):
    for i in range(0, len(rows), B):
        ch = rows[i:i+B]
        out.append(f"INSERT INTO {table} ({','.join(cols)}) VALUES " +
                   ",".join("("+",".join(sv(r[c]) for c in cols)+")" for r in ch) + ";")

cur.execute("SELECT * FROM collection_period WHERE grain_id=2 AND period_date=%s ORDER BY period_id", (DATE,))
cp = cur.fetchall(); old=[r['period_id'] for r in cp]
remap = {o: BASE+i+1 for i,o in enumerate(old)}
iq=",".join(["%s"]*len(old))
cur.execute(f"SELECT * FROM collection_dpd WHERE period_id IN ({iq})", old); cd=cur.fetchall()
cur.execute(f"SELECT * FROM collection_npa WHERE period_id IN ({iq})", old); cn=cur.fetchall()
for r in cp: r['period_id']=remap[r['period_id']]
for r in cd: r['period_id']=remap[r['period_id']]
for r in cn: r['period_id']=remap[r['period_id']]
out=["START TRANSACTION;"]
out.append(f"DELETE x FROM collection_dpd x JOIN collection_period p ON x.period_id=p.period_id WHERE p.grain_id=2 AND p.period_date='{DATE}';")
out.append(f"DELETE x FROM collection_npa x JOIN collection_period p ON x.period_id=p.period_id WHERE p.grain_id=2 AND p.period_date='{DATE}';")
out.append(f"DELETE FROM collection_period WHERE grain_id=2 AND period_date='{DATE}';")
emit(out,'collection_period',['period_id','employee_id','product_type_id','grain_id','period_date','period_hour','npa_cases','created_at','fy_id','updated_at','created_by','updated_by'],cp)
emit(out,'collection_dpd',['period_id','bucket_id','demand_count','demand_amt','collection_count','collection_amt'],cd)
emit(out,'collection_npa',['period_id','action_id','accounts','amount'],cn)
out.append("COMMIT;")
open(r"C:/Users/nlpl it/Desktop/nlpl_Status/_push_collection_0619.sql","w",encoding="utf-8").write("\n".join(out)+"\n")
print(f"06-19 collection: period={len(cp)} dpd={len(cd)} npa={len(cn)} -> new ids {BASE+1}..{BASE+len(cp)}")
cur.close(); conn.close()
