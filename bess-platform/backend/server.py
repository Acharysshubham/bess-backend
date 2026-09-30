"""BESS platform API. Standard library only. Bearer-token auth (no cookies, so CSRF-immune), PBKDF2 passwords, sqlite, per-user scoping on every query."""
import os, json, re, sqlite3, hmac, hashlib, base64, time, uuid, threading, secrets, csv, io, copy, logging
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import calc
DB = os.environ.get('BESS_DB','bess.db'); SECRET = (os.environ.get('BESS_SECRET') or secrets.token_hex(32)).encode()
CORS = os.environ.get('BESS_CORS_ORIGIN',''); RATE = int(os.environ.get('BESS_RATE','120')); AUTHRATE = int(os.environ.get('BESS_AUTH_RATE','10'))
HERE = os.path.dirname(os.path.abspath(__file__)); log = logging.getLogger('bess')
_hits = {}; _lock = threading.Lock()
class Err(Exception):
    def __init__(s,code,msg): s.code, s.msg = code, msg
def conn():
    c = sqlite3.connect(DB, timeout=15, isolation_level=None); c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA foreign_keys=ON'); return c
def init():
    c = conn(); c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,pw TEXT NOT NULL,created REAL);
    CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,created REAL);
    CREATE TABLE IF NOT EXISTS versions(project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,n INTEGER NOT NULL,inputs TEXT NOT NULL,results TEXT NOT NULL,created REAL,PRIMARY KEY(project_id,n));
    CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,action TEXT,project_id TEXT,ts REAL);
    CREATE INDEX IF NOT EXISTS ix_p ON projects(user_id);''')
def audit(c,uid,a,pid=None): c.execute('INSERT INTO audit_logs(user_id,action,project_id,ts) VALUES(?,?,?,?)',(uid,a,pid,time.time()))
def hashpw(p,salt=None):
    salt = salt or os.urandom(16); return salt.hex()+'$'+hashlib.pbkdf2_hmac('sha256',p.encode(),salt,200_000).hex()
def checkpw(p,h): salt = bytes.fromhex(h.split('$')[0]); return hmac.compare_digest(hashpw(p,salt),h)
def mint(uid): b = base64.urlsafe_b64encode(json.dumps({'u':uid,'exp':time.time()+8*3600}).encode()).decode(); return b+'.'+hmac.new(SECRET,b.encode(),'sha256').hexdigest()
def verify(t):
    try:
        b,sig = t.split('.'); assert hmac.compare_digest(sig,hmac.new(SECRET,b.encode(),'sha256').hexdigest())
        d = json.loads(base64.urlsafe_b64decode(b)); assert d['exp'] > time.time(); return d['u']
    except Exception: raise Err(401,'invalid or expired token')
def limit(ip,cap):
    now = time.time()
    with _lock:
        L = [t for t in _hits.get((ip,cap),[]) if now-t < 60]
        if len(L) >= cap: raise Err(429,'rate limit exceeded')
        L.append(now); _hits[(ip,cap)] = L
def summary(res):
    r = res['rows']; y1 = next((x for x in r if x['year']==1), r[0])
    return {'pass':res['pass'],'poi_y1':y1['poi_disch'],'poi_last':r[-1]['poi_disch'],'rte_last':r[-1]['rte'],'years_ok':sum(x['pass'] for x in r),'years':len(r)}
def own(c,pid,uid):
    p = c.execute('SELECT * FROM projects WHERE id=? AND user_id=?',(pid,uid)).fetchone()
    if not p: raise Err(404,'project not found')
    return p
def latest(c,pid):
    return c.execute('SELECT * FROM versions WHERE project_id=? ORDER BY n DESC LIMIT 1',(pid,)).fetchone()
def save_version(c,pid,inputs):
    s = calc.prepare(inputs); res = calc.calc(s); c.execute('BEGIN IMMEDIATE')
    try:
        n = (c.execute('SELECT MAX(n) m FROM versions WHERE project_id=?',(pid,)).fetchone()['m'] or 0)+1
        c.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(pid,n,json.dumps(s),json.dumps(res),time.time())); c.execute('COMMIT')
    except Exception: c.execute('ROLLBACK'); raise
    return n,s,res
def setpath(o,p,v):
    k = p.split('.')
    for x in k[:-1]: o = o[x]
    o[k[-1]] = v
class H(BaseHTTPRequestHandler):
    server_version = 'BESS'; protocol_version = 'HTTP/1.1'
    def log_message(self,*a): pass
    def out(self,code,body,ct='application/json',extra=None):
        b = body if isinstance(body,bytes) else json.dumps(body).encode(); self.send_response(code)
        for k,v in {'Content-Type':ct,'Content-Length':str(len(b)),'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Cache-Control':'no-store',
          'Content-Security-Policy':"default-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com",**(extra or {})}.items(): self.send_header(k,v)
        if CORS: self.send_header('Access-Control-Allow-Origin',CORS); self.send_header('Access-Control-Allow-Headers','Authorization,Content-Type'); self.send_header('Access-Control-Allow-Methods','GET,POST,PUT,DELETE')
        self.end_headers(); self.wfile.write(b)
    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 2_000_000: raise Err(413,'body too large')
        try: d = json.loads(self.rfile.read(n) or b'{}'); assert isinstance(d,dict); return d
        except Exception: raise Err(400,'invalid JSON object')
    def do_OPTIONS(self): self.out(204,b'')
    def do_GET(self): self.route('GET')
    def do_POST(self): self.route('POST')
    def do_PUT(self): self.route('PUT')
    def do_DELETE(self): self.route('DELETE')
    def route(self,m):
        try:
            u = urlparse(self.path); path = u.path; q = parse_qs(u.query); ip = self.client_address[0]
            if m == 'GET' and path in ('/','/index.html'):
                return self.out(200,open(os.path.join(HERE,'static','index.html'),'rb').read(),'text/html; charset=utf-8')
            if not path.startswith('/api/'): raise Err(404,'not found')
            limit(ip,RATE); c = conn()
            if path in ('/api/register','/api/login') and m == 'POST':
                limit(ip,AUTHRATE); d = self.body(); e = str(d.get('email','')).strip().lower(); pw = str(d.get('password',''))
                if not re.fullmatch(r'[^@\s]{1,64}@[^@\s]{1,255}',e) or len(pw) < 8: raise Err(422,'valid email and password of 8+ characters required')
                if path.endswith('register'):
                    uid = str(uuid.uuid4())
                    try: c.execute('INSERT INTO users VALUES(?,?,?,?)',(uid,e,hashpw(pw),time.time()))
                    except sqlite3.IntegrityError: raise Err(409,'email already registered')
                else:
                    r = c.execute('SELECT * FROM users WHERE email=?',(e,)).fetchone()
                    if not r or not checkpw(pw,r['pw']): raise Err(401,'wrong email or password')
                    uid = r['id']
                audit(c,uid,path[5:]); return self.out(200,{'token':mint(uid)})
            a = self.headers.get('Authorization','')
            uid = verify(a[7:] if a.startswith('Bearer ') else '')
            if path == '/api/projects':
                if m == 'GET': return self.out(200,[dict(r) for r in c.execute('SELECT id,name,created FROM projects WHERE user_id=? ORDER BY created DESC',(uid,))])
                if m == 'POST':
                    d = self.body(); name = str(d.get('name','')).strip()[:120]
                    if not name: raise Err(422,'name required')
                    pid = str(uuid.uuid4()); c.execute('INSERT INTO projects VALUES(?,?,?,?)',(pid,uid,name,time.time()))
                    n,s,res = save_version(c,pid,d.get('inputs') or {}); audit(c,uid,'create',pid); return self.out(201,{'id':pid,'version':n,'summary':summary(res)})
            mm = re.fullmatch(r'/api/projects/([0-9a-f-]{36})(?:/(\w+))?',path)
            if not mm: raise Err(404,'not found')
            pid, act = mm.groups(); p = own(c,pid,uid); v = latest(c,pid); inputs = json.loads(v['inputs'])
            if act is None and m == 'GET': return self.out(200,{'id':pid,'name':p['name'],'version':v['n'],'inputs':inputs})
            if act is None and m == 'PUT':
                n,s,res = save_version(c,pid,self.body().get('inputs') or {}); audit(c,uid,'update',pid); return self.out(200,{'version':n,'summary':summary(res)})
            if act is None and m == 'DELETE': c.execute('DELETE FROM projects WHERE id=?',(pid,)); audit(c,uid,'delete',pid); return self.out(200,{'deleted':True})
            if act == 'calculate' and m == 'POST': return self.out(200,calc.calc(calc.prepare(inputs)))
            if act == 'results' and m == 'GET': return self.out(200,json.loads(v['results']))
            if act == 'optimize' and m == 'POST':
                s = calc.prepare(inputs); return self.out(200,{**calc.size_start(s),'min_containers_all_years':calc.min_containers(s)})
            if act == 'sensitivity' and m == 'POST':
                d = self.body(); t = copy.deepcopy(inputs); k = str(d.get('path',''))
                if k == 'sohShift':
                    for r in t['rows']: r['soh'] = min(100,r['soh']+float(d.get('value',0)))
                else:
                    try: setpath(t,k,d.get('value'))
                    except (KeyError,TypeError): raise Err(422,'unknown parameter path')
                b = calc.prepare(inputs); n2 = calc.prepare(t)
                return self.out(200,{'base':{**summary(calc.calc(b)),'min_containers':calc.min_containers(b)},'changed':{**summary(calc.calc(n2)),'min_containers':calc.min_containers(n2)}})
            if act == 'versions' and m == 'GET':
                return self.out(200,[{'n':r['n'],'created':r['created'],**summary(json.loads(r['results']))} for r in c.execute('SELECT n,results,created FROM versions WHERE project_id=? ORDER BY n',(pid,))])
            if act == 'compare' and m == 'GET':
                A,Bv = [c.execute('SELECT * FROM versions WHERE project_id=? AND n=?',(pid,int(q.get(k,['0'])[0]))).fetchone() for k in ('a','b')]
                if not A or not Bv: raise Err(404,'version not found')
                ia,ib = json.loads(A['inputs']),json.loads(Bv['inputs'])
                diff = {k:[ia.get(k),ib.get(k)] for k in set(ia)|set(ib) if k != 'rows' and ia.get(k) != ib.get(k)}
                return self.out(200,{'input_changes':diff,'a':summary(json.loads(A['results'])),'b':summary(json.loads(Bv['results']))})
            if act == 'report' and m == 'GET':
                rows = json.loads(v['results'])['rows']; cols = ['label','dc_mwh','poi_disch','poi_charge','rte','rte_ex_aux','poi_mw','crate','pass']
                if q.get('fmt',['csv'])[0] == 'xlsx':
                    try:
                        import openpyxl; wb = openpyxl.Workbook(); ws = wb.active; ws.append(cols); [ws.append([r[k] for k in cols]) for r in rows]
                        ws2 = wb.create_sheet('Inputs'); [ws2.append([k,json.dumps(x)]) for k,x in inputs.items() if k != 'rows']
                        bio = io.BytesIO(); wb.save(bio); return self.out(200,bio.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    except ImportError: raise Err(501,'xlsx needs openpyxl; use fmt=csv')
                sio = io.StringIO(); w = csv.writer(sio); w.writerow(cols); [w.writerow([r[k] for k in cols]) for r in rows]
                return self.out(200,sio.getvalue().encode(),'text/csv')
            raise Err(404,'not found')
        except Err as e: self.out(e.code,{'error':e.msg})
        except calc.ValidationError as e: self.out(422,{'error':str(e)})
        except Exception: log.exception('unhandled'); self.out(500,{'error':'internal error'})
def make_server(host='127.0.0.1',port=8000): init(); return ThreadingHTTPServer((host,port),H)
if __name__ == '__main__':
    h = os.environ.get('BESS_HOST','127.0.0.1'); p = int(os.environ.get('BESS_PORT','8000')); print('BESS on http://%s:%d'%(h,p)); os.environ.get('BESS_SECRET') or print('WARNING: BESS_SECRET not set; logins reset on every restart.'); make_server(h,p).serve_forever()
