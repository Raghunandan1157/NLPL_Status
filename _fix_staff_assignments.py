"""Fix missing region mapping: some employees have collection data but NO current
employee_assignment (ended_at IS NULL), so they drop out of the by-region breakdown
(growwithme HIER_JOIN -> region_name NULL -> excluded). Create a current assignment
(employee -> branch) for each, using imp_pg_employees.branch_id (the live source's
emp->branch map). LOCAL only; writes a remapped AWS push file too. Idempotent
(created_by='staff_fix')."""
import pymysql
PWFILE = r"C:/Users/nlpl it/Desktop/growwithme-local/database/.env"
PW = next(l.split('=',1)[1].strip() for l in open(PWFILE, encoding='utf-8') if l.upper().startswith('MYSQL_PASSWORD='))
conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password=PW,
                       database='Growwithme_NEWDB', charset='utf8mb4', autocommit=False)
cur = conn.cursor(pymysql.cursors.DictCursor)

# employees that appear in recent daily collection but have no current assignment
cur.execute("""
  SELECT DISTINCT cp.employee_id, e.employee_code
  FROM collection_period cp
  JOIN employee e ON e.employee_id = cp.employee_id
  LEFT JOIN employee_assignment a ON a.employee_id = cp.employee_id AND a.ended_at IS NULL
  WHERE cp.grain_id=2 AND cp.period_date >= '2026-06-16' AND a.employee_id IS NULL
""")
missing = cur.fetchall()

# emp_code -> branch_id from the live source (imp_pg_employees)
cur.execute("SELECT emp_id, branch_id FROM imp_pg_employees WHERE branch_id IS NOT NULL")
src = {r['emp_id']: r['branch_id'] for r in cur.fetchall()}
# valid growwithme branch ids
cur.execute("SELECT branch_id FROM branch")
valid_branch = {r['branch_id'] for r in cur.fetchall()}

cur.execute("SELECT COALESCE(MAX(assignment_id),0) m FROM employee_assignment")
aid = cur.fetchone()['m']
cur.execute("SELECT NOW() n"); NOW = cur.fetchone()['n']

rows, unresolved = [], []
for r in missing:
    b = src.get(r['employee_code'])
    if b is None or b not in valid_branch:
        unresolved.append(r['employee_code']); continue
    aid += 1
    rows.append((aid, r['employee_id'], b, None, None, None, NOW, None, NOW, NOW, 'staff_fix', None))

# idempotent: drop any prior staff_fix assignments for these employees
emp_ids = [r['employee_id'] for r in missing]
if emp_ids:
    iq = ",".join(["%s"]*len(emp_ids))
    cur.execute(f"DELETE FROM employee_assignment WHERE created_by='staff_fix' AND employee_id IN ({iq})", emp_ids)

cur.executemany("""INSERT INTO employee_assignment
  (assignment_id, employee_id, branch_id, designation_id, role_id, reports_to_employee_id,
   started_at, ended_at, created_at, updated_at, created_by, updated_by)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
conn.commit()
print(f"missing employees: {len(missing)} | assignments created: {len(rows)} | unresolved: {unresolved}")
print(f"assignment_id range: {rows[0][0]}..{rows[-1][0]}" if rows else "none")
cur.close(); conn.close()
