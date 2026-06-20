"""Stage Postgres pg_dump data into local MySQL Growwithme_NEWDB as imp_* tables.
Read-only against AWS; writes ONLY to the LOCAL MySQL working copy. Excludes
otp_audit (auth/login) per instruction. This is the convert+load step; no AWS push.
"""
import gzip, re, sys
import pymysql

PWFILE = r"C:/Users/nlpl it/Desktop/growwithme-local/database/.env"
PW = None
for line in open(PWFILE, encoding="utf-8"):
    if line.upper().startswith("MYSQL_PASSWORD="):
        PW = line.split("=", 1)[1].strip()
        break

SOURCES = [
    (r"C:/Users/nlpl it/Downloads/portfolio_month_wise_LIVE_2026-06-19.sql.gz", "imp_pmw_"),
    (r"C:/Users/nlpl it/Downloads/postgres_LIVE_2026-06-19.sql.gz", "imp_pg_"),
]
EXCLUDE = {"otp_audit"}  # auth/login — excluded per instruction

PG_TYPE_RE = [
    (re.compile(r'^(character varying|varchar)\s*\((\d+)\)'), lambda m: f"VARCHAR({m.group(2)})"),
    (re.compile(r'^(character varying|varchar)\b'), lambda m: "VARCHAR(255)"),
    (re.compile(r'^character\s*\((\d+)\)'), lambda m: f"CHAR({m.group(1)})"),
    (re.compile(r'^numeric\s*\((\d+),\s*(\d+)\)'), lambda m: f"DECIMAL({m.group(1)},{m.group(2)})"),
    (re.compile(r'^numeric\s*\((\d+)\)'), lambda m: f"DECIMAL({m.group(1)},0)"),
    (re.compile(r'^numeric\b'), lambda m: "DECIMAL(38,10)"),
    (re.compile(r'^bigint\b'), lambda m: "BIGINT"),
    (re.compile(r'^smallint\b'), lambda m: "SMALLINT"),
    (re.compile(r'^(integer|int)\b'), lambda m: "INT"),
    (re.compile(r'^boolean\b'), lambda m: "TINYINT(1)"),
    (re.compile(r'^timestamp\b'), lambda m: "DATETIME(6)"),
    (re.compile(r'^date\b'), lambda m: "DATE"),
    (re.compile(r'^time\b'), lambda m: "TIME"),
    (re.compile(r'^(double precision|real)\b'), lambda m: "DOUBLE"),
    (re.compile(r'^(json|jsonb)\b'), lambda m: "JSON"),
    (re.compile(r'^uuid\b'), lambda m: "CHAR(36)"),
    (re.compile(r'^text\b'), lambda m: "LONGTEXT"),
]

def map_type(coldef):
    cd = coldef.strip().lower()
    for rx, fn in PG_TYPE_RE:
        m = rx.match(cd)
        if m:
            return fn(m)
    return "LONGTEXT"

def unescape(v):
    if v == r'\N':
        return None
    if '\\' not in v:
        return v
    out = []; i = 0; M = {'t':'\t','n':'\n','r':'\r','b':'\b','f':'\f','v':'\v','\\':'\\'}
    while i < len(v):
        c = v[i]
        if c == '\\' and i+1 < len(v):
            out.append(M.get(v[i+1], v[i+1])); i += 2
        else:
            out.append(c); i += 1
    return ''.join(out)

def parse_dump(path):
    """Yield (table, columns[list of (name,mysqltype)], rows_iter_factory). Two-pass:
    first collect CREATE TABLE coltypes, then stream COPY data."""
    coltypes = {}  # table -> {col: mysqltype}
    with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as fh:
        cur = None
        for line in fh:
            if cur is None:
                m = re.match(r'CREATE TABLE public\.("?[\w]+"?) \(', line)
                if m:
                    cur = m.group(1).strip('"'); coltypes[cur] = {}
            else:
                s = line.strip()
                if s.startswith(');'):
                    cur = None; continue
                mm = re.match(r'"?([\w]+)"?\s+(.*?),?\s*$', s)
                if mm and not s.upper().startswith(('CONSTRAINT', 'PRIMARY', 'UNIQUE', 'FOREIGN', 'CHECK')):
                    coltypes[cur][mm.group(1)] = map_type(mm.group(2))
    return coltypes

def main():
    conn = pymysql.connect(host="127.0.0.1", port=3306, user="root", password=PW,
                           database="Growwithme_NEWDB", charset="utf8mb4", autocommit=False,
                           local_infile=False)
    cur = conn.cursor()
    cur.execute("SET SESSION sql_mode=''")  # lenient for staging — avoid hard fails on edge values
    summary = []
    for path, prefix in SOURCES:
        coltypes = parse_dump(path)
        # stream COPY blocks
        with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as fh:
            in_copy = None; cols = None; tbl = None; batch = []; loaded = 0
            insert_sql = None
            def flush():
                nonlocal batch, loaded
                if batch:
                    cur.executemany(insert_sql, batch); loaded += len(batch); batch = []
            for line in fh:
                if in_copy is None:
                    m = re.match(r'COPY public\.("?[\w]+"?) \((.*?)\) FROM stdin;', line)
                    if m:
                        tbl = m.group(1).strip('"')
                        if tbl in EXCLUDE:
                            in_copy = "SKIP"; continue
                        cols = [c.strip().strip('"') for c in m.group(2).split(',')]
                        dest = prefix + tbl
                        ct = coltypes.get(tbl, {})
                        coldefs = ", ".join(f"`{c}` {ct.get(c,'LONGTEXT')} NULL" for c in cols)
                        cur.execute(f"DROP TABLE IF EXISTS `{dest}`")
                        cur.execute(f"CREATE TABLE `{dest}` ({coldefs}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
                        ph = ",".join(["%s"]*len(cols))
                        insert_sql = f"INSERT INTO `{dest}` ({','.join('`'+c+'`' for c in cols)}) VALUES ({ph})"
                        boolcols = {i for i, c in enumerate(cols) if ct.get(c, '') == 'TINYINT(1)'}
                        in_copy = tbl; loaded = 0
                elif in_copy == "SKIP":
                    if line.startswith('\\.'):
                        in_copy = None
                else:
                    if line.startswith('\\.'):
                        flush(); conn.commit()
                        summary.append((prefix + in_copy, loaded))
                        in_copy = None; cols = None
                    else:
                        raw = line.rstrip('\n').split('\t')
                        if len(raw) == len(cols):
                            vals = []
                            for i, x in enumerate(raw):
                                u = unescape(x)
                                if u is not None and i in boolcols:
                                    u = 1 if u in ('t', 'true', 'T', '1') else (0 if u in ('f', 'false', 'F', '0') else u)
                                vals.append(u)
                            batch.append(vals)
                            if len(batch) >= 5000:
                                flush()
        conn.commit()
    cur.close(); conn.close()
    print(f"{'STAGED TABLE':40} {'ROWS':>9}")
    for t, n in summary:
        print(f"{t:40} {n:>9,}")
    print(f"\nTotal tables staged: {len(summary)} | total rows: {sum(n for _,n in summary):,}")
    print("Excluded:", ", ".join(sorted(EXCLUDE)))

main()
