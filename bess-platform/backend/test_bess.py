import os, tempfile, json, threading, unittest, urllib.request, urllib.error, copy
os.environ['BESS_DB'] = tempfile.mktemp(suffix='.db'); os.environ['BESS_SECRET'] = 'test-secret'; os.environ['BESS_RATE'] = '100000'; os.environ['BESS_AUTH_RATE'] = '100000'
import calc, server
BASE = dict(cap=5,N=4,cMW=2.5,auxD=.01,auxC=.01,other=0,standby=0,Td=2,Tc=2,infra=0,avail=98.5,dcEff=98,mism=99,dod=95,reqE=12,reqMW=5,reqRTE=78,cMax=.6,life=15,fat=0,sat=0,aug={'8':1},
  eff=dict(lv=99.5,pcs=98.5,idtc=99.5,idt=99,dcdb=99.8,mv=99.7,mpt=99.3,kv=99.8,meas=99.8),rows=[])
srv = server.make_server('127.0.0.1',0); PORT = srv.server_address[1]; threading.Thread(target=srv.serve_forever,daemon=True).start()
def req(m,p,d=None,t=None):
    r = urllib.request.Request('http://127.0.0.1:%d%s'%(PORT,p),method=m,data=json.dumps(d).encode() if d is not None else None,headers={'Authorization':'Bearer '+t} if t else {})
    try: x = urllib.request.urlopen(r); return x.status,json.loads(x.read() or b'{}') if 'json' in x.headers['Content-Type'] else x.read()
    except urllib.error.HTTPError as e: return e.code,json.loads(e.read())
n = [0]
def user():
    n[0] += 1; return req('POST','/api/register',{'email':'u%d@x.com'%n[0],'password':'password123'})[1]['token']
class Engine(unittest.TestCase):
    def setUp(self): self.s = calc.prepare(BASE)
    def test_docx_nameplate_example(self): self.assertAlmostEqual(calc.nameplate_example(.75,.9,.95,.8),1.0965,3)
    def test_container_sizing_max(self):
        z = calc.size_start(self.s); self.assertEqual(z['n_required'],max(z['n_energy'],z['n_power']))
    def test_chain_and_dc_usable(self):
        c = calc.calc(self.s); r = c['rows'][0]; f = .98*.99*.95*.984*.996
        self.assertAlmostEqual(r['dc_mwh'],5*4*.985*f,9); self.assertAlmostEqual(r['disch_ex_aux'],r['dc_mwh']*c['chain'],9)
    def test_aux_and_charge(self):
        r = calc.calc(self.s)['rows'][0]; self.assertAlmostEqual(r['disch_in_aux'],r['disch_ex_aux']-4*.01*2,9)
        self.assertAlmostEqual(r['charge_in_aux'],r['charge_ex_aux']+4*.01*2,9)
    def test_rte_ex_aux_definition(self):
        c = calc.calc(self.s); r = c['rows'][0]; self.assertAlmostEqual(r['rte_ex_aux'],r['disch_ex_aux']/r['charge_ex_aux']*100,9)
    def test_augmentation_zero_before_year(self):
        rows = calc.calc(self.s)['rows']; self.assertTrue(all(x['aug_containers']==0 for x in rows if x['year']<8)); self.assertEqual(rows[8-1]['aug_containers'],1)
    def test_fat_sat_rows(self):
        s = calc.prepare({**BASE,'fat':3,'sat':6}); self.assertEqual([r['l'] for r in s['rows'][:3]],['FAT','SAT','Y1'])
    def test_dcdc_rte_variable(self):
        s = copy.deepcopy(self.s); s['rows'][0]['rte'] = 90; self.assertLess(calc.calc(s)['rows'][0]['rte'],calc.calc(self.s)['rows'][0]['rte'])
    def test_compliance_and_min_n(self):
        m = calc.min_containers(self.s); self.assertTrue(calc.calc({**self.s,'N':m})['pass']); self.assertFalse(calc.calc({**self.s,'N':m-1})['pass'] if m>1 else False)
    def test_crate_fail(self):
        self.assertFalse(calc.calc({**self.s,'cMax':.05})['rows'][0]['ok']['crate'])
    def test_pcs_and_cost(self):
        self.assertEqual(calc.pcs_size(5,2.5,100,1)['units'],3)
        e = calc.economics(self.s,dict(pcsMW=2.5,costCont=100,costPcs=10,bop=50,costAug=90)); self.assertEqual(e['initial_capex'],4*100+2*10+50); self.assertEqual(e['augmentation_capex'],90)
    def test_load_profile(self):
        p = calc.load_profile([2,3,3,2,3],15,2.5); self.assertAlmostEqual(p['energy_above_target_mwh'],.25*(.5*3),9); self.assertEqual(p['events'],2)
    def test_validation(self):
        for bad in ({'dod':150},{'N':0},{'fat':7}): 
            with self.assertRaises(calc.ValidationError): calc.prepare({**BASE,**bad})
class Api(unittest.TestCase):
    def test_serves_ui(self):
        c,b = req('GET','/'); self.assertEqual(c,200); self.assertIn(b'Account',b); self.assertIn(b'/api/projects',b)
    def test_project_names_stored_verbatim(self):
        a = user(); c,r = req('POST','/api/projects',{'name':'<img src=x onerror=alert(1)>','inputs':BASE},a); self.assertEqual(c,201)
        self.assertEqual(req('GET','/api/projects',None,a)[1][0]['name'],'<img src=x onerror=alert(1)>')
    def test_auth_required(self): self.assertEqual(req('GET','/api/projects')[0],401)
    def test_weak_password(self): self.assertEqual(req('POST','/api/register',{'email':'a@b.com','password':'x'})[0],422)
    def test_bad_login(self): user(); self.assertEqual(req('POST','/api/login',{'email':'u1@x.com','password':'wrongwrong'})[0],401)
    def test_isolation(self):
        a,b = user(),user(); c,r = req('POST','/api/projects',{'name':'A','inputs':BASE},a); pid = r['id']
        self.assertEqual(req('GET','/api/projects',None,b)[1],[])
        for m,p in (('GET',''),('DELETE',''),('POST','/calculate'),('GET','/results'),('GET','/report')): self.assertEqual(req(m,'/api/projects/'+pid+p,{} if m=='POST' else None,b)[0],404)
        self.assertEqual(req('PUT','/api/projects/'+pid,{'inputs':BASE},b)[0],404); self.assertEqual(req('GET','/api/projects/'+pid,None,a)[0],200)
    def test_versioning_compare_sensitivity_report(self):
        a = user(); pid = req('POST','/api/projects',{'name':'V','inputs':BASE},a)[1]['id']
        self.assertEqual(req('PUT','/api/projects/'+pid,{'inputs':{**BASE,'dod':85}},a)[1]['version'],2)
        c,d = req('GET','/api/projects/%s/compare?a=1&b=2'%pid,None,a); self.assertEqual(d['input_changes']['dod'],[95,85]); self.assertLess(d['b']['poi_y1'],d['a']['poi_y1'])
        c,d = req('POST','/api/projects/%s/sensitivity'%pid,{'path':'eff.pcs','value':99},a); self.assertGreater(d['changed']['poi_y1'],d['base']['poi_y1'])
        self.assertEqual(req('GET','/api/projects/%s/report?fmt=xlsx'%pid,None,a)[0],200); self.assertEqual(req('POST','/api/projects/%s/optimize'%pid,{},a)[0],200)
    def test_api_rejects_invalid(self):
        a = user(); self.assertEqual(req('POST','/api/projects',{'name':'x','inputs':{**BASE,'dod':500}},a)[0],422)
    def test_concurrent_writes_same_project(self):
        a = user(); pid = req('POST','/api/projects',{'name':'C','inputs':BASE},a)[1]['id']; got = []
        def w(i): got.append(req('PUT','/api/projects/'+pid,{'inputs':{**BASE,'reqE':10+i*.1}},a)[1]['version'])
        T = [threading.Thread(target=w,args=(i,)) for i in range(20)]; [t.start() for t in T]; [t.join() for t in T]
        self.assertEqual(sorted(got),list(range(2,22)))
    def test_concurrent_users_no_crosstalk(self):
        res = {}
        def w(i):
            t = user(); pid = req('POST','/api/projects',{'name':'P%d'%i,'inputs':{**BASE,'reqE':10+i}},t)[1]['id']; res[i] = req('GET','/api/projects/'+pid,None,t)[1]['inputs']['reqE']
        T = [threading.Thread(target=w,args=(i,)) for i in range(12)]; [t.start() for t in T]; [t.join() for t in T]
        self.assertEqual(res,{i:10+i for i in range(12)})
    def test_expired_or_tampered_token(self): self.assertEqual(req('GET','/api/projects',None,user()+'x')[0],401)
if __name__ == '__main__': unittest.main(verbosity=1)
