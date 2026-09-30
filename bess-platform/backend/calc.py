"""BESS calculation engine. Pure functions, no shared state. Formulas follow BESS SIZING SOFTWARE.docx."""
import math, copy
EFF_KEYS = ['lv','pcs','idtc','idt','dcdb','mv','mpt','kv','meas']
RANGES = {'cap':(0.01,1e4),'N':(1,1e4),'cMW':(0.01,1e4),'auxD':(0,100),'auxC':(0,100),'other':(0,100),'standby':(0,100),
 'infra':(0,100),'Td':(0.01,48),'Tc':(0.01,48),'avail':(1,100),'dcEff':(1,100),'mism':(1,100),'dod':(1,100),'reqE':(0,1e6),
 'reqMW':(0,1e5),'reqRTE':(0,100),'cMax':(0.01,10),'life':(1,40),'fat':(0,6),'sat':(0,6)}
class ValidationError(ValueError): pass
def _num(v): return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def build_rows(s, old=None):
    old = {r['l']: r for r in (old or [])}; L = []
    if s.get('fat',0) > 0: L.append(('FAT',0))
    if s.get('sat',0) > 0: L.append(('SAT',0))
    L += [('Y%d'%y,y) for y in range(1,int(s['life'])+1)]
    return [old.get(l) or {'l':l,'y':y,'soh':100 if not y else round(100-1.6*y,1),'cal':100 if not y else round(100-.4*y,1),'rte':98,'req':s['reqE']} for l,y in L]
def validate(s):
    for k,(lo,hi) in RANGES.items():
        if k not in s or not _num(s[k]) or not lo <= s[k] <= hi: raise ValidationError('%s must be a number in [%s, %s]'%(k,lo,hi))
    e = s.get('eff') or {}
    for k in EFF_KEYS:
        if not _num(e.get(k)) or not 1 <= e[k] <= 100: raise ValidationError('eff.%s must be in [1, 100]'%k)
    for r in s.get('rows') or []:
        for k in ('soh','cal','rte'):
            if not _num(r.get(k)) or not 1 <= r[k] <= 100: raise ValidationError('%s %s must be in [1, 100]'%(r.get('l'),k))
        if not _num(r.get('req')) or r['req'] < 0: raise ValidationError('%s req must be >= 0'%r.get('l'))
    for y,n in (s.get('aug') or {}).items():
        if not str(y).isdigit() or not 1 <= int(y) <= s['life'] or not _num(n) or n < 0 or int(n) != n:
            raise ValidationError('augmentation year must be 1..life and containers a non-negative integer')
def prepare(s):
    s = copy.deepcopy(s); s.setdefault('aug',{}); s.setdefault('rows',[])
    validate({**s,'rows':[]}); s['rows'] = build_rows(s, s['rows']); validate(s); return s
P = lambda v: v/100
def calc(s):
    chain = 1.0
    for k in EFF_KEYS: chain *= P(s['eff'][k])
    aD = s['N']*s['auxD']+s['other']+s['standby']; aC = s['N']*s['auxC']+s['other']+s['standby']; out = []
    for r in s['rows']:
        f = P(s['dcEff'])*P(s['mism'])*P(s['dod'])*P(r['soh'])*P(r['cal']); dc = s['cap']*s['N']*P(s['avail'])*f
        dEx = dc*chain; dIn = dEx - aD*s['Td']; cEx = dc/chain/P(r['rte']); cIn = cEx + aC*s['Tc']
        an = sum(int(n) for y,n in s['aug'].items() if int(y) <= r['y']) if r['y'] >= 1 else 0; ac = an*s['cap']*f
        xD = ac*chain - an*s['auxD']*s['Td']; xC = ac/chain/P(r['rte']) + an*s['auxC']*s['Tc']
        totD = dIn+xD-s['infra']*s['Td']; totC = cIn+xC+s['infra']*s['Tc']; rte = totD/totC*100
        rteEx = (dEx+ac*chain)/(cEx+ac/chain/P(r['rte']))*100
        mw = (s['N']+an)*s['cMW']*chain; cr = s['reqMW']/((s['N']+an)*s['cap'])
        ok = {'energy':totD >= r['req'],'rte':rte >= s['reqRTE'],'power':mw >= s['reqMW'],'crate':cr <= s['cMax']}
        out.append({'label':r['l'],'year':r['y'],'dc_mwh':dc,'disch_ex_aux':dEx,'disch_in_aux':dIn,'charge_ex_aux':cEx,'charge_in_aux':cIn,
          'aug_containers':an,'poi_disch':totD,'poi_charge':totC,'rte':rte,'rte_ex_aux':rteEx,'poi_mw':mw,'crate':cr,'ok':ok,'pass':all(ok.values())})
    return {'chain':chain,'rows':out,'pass':all(o['pass'] for o in out)}
def min_containers(s, hi=300):
    for n in range(1,hi+1):
        if calc({**s,'N':n})['pass']: return n
def nameplate_example(usable, dod, eff, eol): return usable/(dod*eff*eol)
def size_start(s):
    eol = min(P(r['soh'])*P(r['cal']) for r in s['rows']); need = s['reqE']/(P(s['dod'])*P(s['dcEff'])*eol)
    ne = math.ceil(need/s['cap']); npw = math.ceil(s['reqMW']/s['cMW']); return {'nameplate_mwh':need,'n_energy':ne,'n_power':npw,'n_required':max(ne,npw)}
def load_profile(vals, interval_min, target):
    h = interval_min/60; over = [v-target for v in vals if v > target]; runs = []; cur = 0
    for v in vals:
        if v > target: cur += (v-target)*h
        elif cur: runs.append(cur); cur = 0
    if cur: runs.append(cur)
    return {'peak':max(vals),'min':min(vals),'avg':sum(vals)/len(vals),'required_mw':max(max(vals)-target,0),'energy_above_target_mwh':sum(over)*h,
            'hours_above':len(over)*h,'events':len(runs),'largest_event_mwh':max(runs or [0])}
def pcs_size(req_mw, unit_mw, overload_pct=100, redundant=0, battery_mw=None):
    n = math.ceil(req_mw/(unit_mw*overload_pct/100))+redundant; tot = n*unit_mw
    return {'units':n,'total_mw':tot,'utilisation_pct':req_mw/tot*100,'dc_ac_ratio':(battery_mw/tot) if battery_mw else None}
def economics(s, c):
    pcs = pcs_size(s['reqMW'],c['pcsMW'],c.get('pcsOver',100),c.get('pcsRed',0)); aug = sum(int(n) for n in s['aug'].values())
    init = s['N']*c['costCont']+pcs['units']*c['costPcs']+c.get('bop',0); a = aug*c.get('costAug',c['costCont'])
    return {'initial_capex':init,'augmentation_capex':a,'total_capex':init+a,'per_mwh':(init+a)/(s['N']*s['cap']),'per_mw':(init+a)/s['reqMW'] if s['reqMW'] else None,'pcs':pcs}
