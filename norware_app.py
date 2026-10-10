#!/usr/bin/env python3
"""NORWARE local warehouse demo server. Python standard library only."""
import csv, hashlib, hmac, json, os, re, secrets, sqlite3, time, urllib.parse, urllib.request, urllib.error, getpass, sys, tempfile
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('NORWARE_DB_PATH', str(HERE / 'norware.sqlite3')))
HOST = os.environ.get('NORWARE_HOST', '127.0.0.1')
PORT = int(os.environ.get('NORWARE_PORT', '8765'))
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY','')
OPENAI_MODEL = os.environ.get('NORWARE_AI_MODEL','gpt-6-astra')
DEMO_USER = os.environ.get('NORWARE_DEMO_USER', 'staff.demo')
DEMO_PASSWORD_OVERRIDE = os.environ.get('NORWARE_DEMO_PASSWORD')
DEMO_DB_PATH = None
LEGACY_DEMO_PASSWORD_HASH = '6e6f72776172652d64656d6f2d303031:7669d27b07479a4ef5b6c16dabc7f01e6e00adb9f1e2d1eb86e750a303621b30'
SESSIONS = {}
SESSION_TTL = 30 * 60
WAREHOUSE_SEEDS = [
 ('WH001','Ambient General','US','Chicago','AMBIENT','General ambient goods'),
 ('WH002','Food & Cold Chain','NL','Rotterdam','CHILLED','Food and temperature controlled'),
 ('WH003','Electronics & Secure','SG','Singapore','DRY_SECURE','Electronics and secure dry goods'),
 ('WH004','Hazardous Materials','DE','Hamburg','HAZMAT','Restricted compatible materials'),
 ('WH005','Building Materials','IN','Mumbai','AMBIENT','Building and bulk goods'),
 ('WH006','Healthcare Supply','JP','Osaka','CONTROLLED','Healthcare supply inventory'),
 ('WH007','Waste','IN','Mumbai','WASTE','Separated waste streams; configurable controls')]
WASTE_CONFIG_PATH = Path(os.environ.get('NORWARE_WASTE_CONFIG', str(HERE / 'waste_config.json')))

def load_waste_streams():
    with WASTE_CONFIG_PATH.open(encoding='utf-8') as config_file:
        config=json.load(config_file)
    streams=config.get('waste_streams')
    if not isinstance(streams,list) or not streams:
        raise ValueError('Waste configuration must define a non-empty waste_streams list.')
    names=set()
    for stream in streams:
        if not isinstance(stream,dict) or not all(stream.get(key) for key in ('category','storage','roles')):
            raise ValueError('Each configured waste stream needs category, storage, and roles.')
        if stream['category'] in names or not isinstance(stream['roles'],list) or not set(stream['roles']).issubset({'operator','manager','admin'}):
            raise ValueError('Waste stream categories must be unique and roles must be operator, manager, or admin.')
        names.add(stream['category'])
        stream['restricted']=bool(stream.get('restricted',False))
    return streams

WASTE_STREAMS = load_waste_streams()
WASTE_CATEGORIES = [stream['category'] for stream in WASTE_STREAMS]

def waste_stream(category):
    return next((stream for stream in WASTE_STREAMS if stream['category']==category),None)

def location_allowed(shelf, category, role):
    if shelf['warehouse_id']=='WH007':
        stream=waste_stream(category)
        return bool(stream and role in stream['roles'])
    return not shelf['restricted'] or role in ('manager','admin')

def connect(path=DB_PATH):
    c=sqlite3.connect(path, timeout=15); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def pw_hash(password, salt=None):
    salt=salt or secrets.token_bytes(16)
    return salt.hex()+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt,240000).hex()
def pw_check(password, encoded):
    try:
        salt,expected=encoded.split(':',1); got=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),240000).hex()
        return hmac.compare_digest(got,expected)
    except Exception: return False
def init_db(path=DB_PATH, seed_demo_data=False, seed_users=True):
    c=connect(path)
    c.executescript('''
    CREATE TABLE IF NOT EXISTS warehouses(id TEXT PRIMARY KEY,name TEXT,country TEXT,city TEXT,profile TEXT,description TEXT);
    CREATE TABLE IF NOT EXISTS shelves(id TEXT PRIMARY KEY,warehouse_id TEXT NOT NULL REFERENCES warehouses(id),zone TEXT,category_codes TEXT,storage TEXT,max_length REAL,max_width REAL,max_height REAL,capacity_l REAL,max_weight REAL,level INTEGER,access_easy INTEGER,status TEXT DEFAULT 'Available',restricted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,sku TEXT UNIQUE,barcode TEXT UNIQUE,name TEXT,category_code TEXT,category TEXT,uom TEXT,length REAL,width REAL,height REAL,weight REAL,storage TEXT,priority INTEGER,fragile INTEGER,hazard TEXT,source TEXT DEFAULT 'GENERATED_EXAMPLE');
    CREATE TABLE IF NOT EXISTS shelf_labels(shelf_id TEXT PRIMARY KEY REFERENCES shelves(id),barcode TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS lots(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id TEXT REFERENCES products(id),lot_code TEXT,expiry TEXT,qty INTEGER NOT NULL DEFAULT 0,reserved INTEGER NOT NULL DEFAULT 0,shelf_id TEXT REFERENCES shelves(id),quarantined INTEGER NOT NULL DEFAULT 0,quarantine_reason TEXT,disposition TEXT NOT NULL DEFAULT 'USEFUL');
    CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,product_id TEXT REFERENCES products(id),lot_code TEXT,qty INTEGER,source_shelf TEXT,dest_shelf TEXT,reason TEXT,actor TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS transfer_orders(id TEXT PRIMARY KEY,movement_id INTEGER REFERENCES movements(id),lot_id INTEGER REFERENCES lots(id),product_id TEXT REFERENCES products(id),lot_code TEXT,qty INTEGER NOT NULL,source_shelf TEXT,dest_shelf TEXT,reference TEXT,state TEXT NOT NULL DEFAULT 'IN_TRANSIT',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL,role TEXT NOT NULL,force_change INTEGER NOT NULL DEFAULT 1,active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT,action TEXT,entity TEXT,details TEXT,created_at TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'Audit records are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'Audit records are append-only'); END;
    CREATE INDEX IF NOT EXISTS lots_shelf_idx ON lots(shelf_id); CREATE INDEX IF NOT EXISTS products_barcode_idx ON products(barcode);
    ''')
    movement_columns={r['name'] for r in c.execute('PRAGMA table_info(movements)')}
    if 'status' not in movement_columns: c.execute("ALTER TABLE movements ADD COLUMN status TEXT NOT NULL DEFAULT 'COMPLETED'")
    if 'reference' not in movement_columns: c.execute('ALTER TABLE movements ADD COLUMN reference TEXT')
    lot_columns={r['name'] for r in c.execute('PRAGMA table_info(lots)')}
    if 'disposition' not in lot_columns: c.execute("ALTER TABLE lots ADD COLUMN disposition TEXT NOT NULL DEFAULT 'USEFUL'")
    if 'waste_category' not in lot_columns: c.execute('ALTER TABLE lots ADD COLUMN waste_category TEXT')
    c.execute("UPDATE lots SET waste_category=(SELECT category FROM products WHERE products.id=lots.product_id) WHERE disposition='WASTE' AND waste_category IS NULL AND EXISTS (SELECT 1 FROM products WHERE products.id=lots.product_id AND products.category_code='WASTE')")
    if c.execute('SELECT count(*) FROM warehouses').fetchone()[0]==0:
        for row in WAREHOUSE_SEEDS: c.execute('INSERT INTO warehouses VALUES(?,?,?,?,?,?)',row)
        # Reuse the six warehouse/120-location sample. Add dimensional and weight limits required for safe fit checks.
        with (HERE/'warehouses.csv').open(encoding='utf-8',newline='') as f:
            for w in csv.DictReader(f): c.execute('INSERT OR IGNORE INTO warehouses VALUES(?,?,?,?,?,?)',(w['warehouse_id'],w['warehouse_name'],w['country_code'],w['city'],w['storage_profile'],'Imported NORWARE sample warehouse'))
        coverage={'WH001':'ALL','WH002':'FOOD,PHAR','WH003':'ELEC,PHAR','WH004':'CHEM','WH005':'BUILD,METAL,AUTO,AGRI','WH006':'PHAR,FOOD'}
        with (HERE/'locations.csv').open(encoding='utf-8',newline='') as f:
            for l in csv.DictReader(f):
                c.execute('INSERT OR IGNORE INTO shelves VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(l['location_id'],l['warehouse_id'],l['zone_code'],coverage.get(l['warehouse_id'],'ALL'),l['storage_profile'],300,300,300,float(l['location_capacity_m3'])*1000,100000,int(l['level']),1 if int(l['level'])<=2 else 0,'Available',1 if l['warehouse_id']=='WH004' else 0))
        # Waste facility starts with separately identifiable streams and empty locations.
        for i,stream in enumerate(WASTE_STREAMS,1):
            cat=stream['category']
            sid='WH007-W%02d'%i
            storage=stream['storage']
            restricted=int(stream['restricted'])
            c.execute('INSERT OR IGNORE INTO shelves VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(sid,'WH007',cat,cat,storage,150,150,180,250000,50000,1,0,'Available',restricted))
        c.execute('INSERT OR IGNORE INTO shelves VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('WH002-FROZEN-01','WH002','Frozen reserve','FOOD','FROZEN',120,120,100,320000,30000,1,1,'Available',0))
        # Import the generated catalog (not its synthetic inventory placements); placements need physical validation.
        with (HERE/'products.csv').open(encoding='utf-8',newline='') as f:
            for p in csv.DictReader(f):
                parent=re.match(r'[A-Z]+',p['category_code']).group(0)
                c.execute('INSERT OR IGNORE INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(p['product_id'],p['sku'],'990'+p['product_id'][2:],p['product_name'],parent,p['material_category'],p['unit_of_measure'],float(p['length_cm']),float(p['width_cm']),float(p['height_cm']),float(p['weight_kg']),p['storage_condition'],int(p['access_priority']),p['fragile']=='true',p['hazard_level'],p['data_origin']))
        if seed_demo_data:
            today=date.today()
            demos=[('DEMO-FOOD-01','D-FOOD-01','8901000000011','Organic oat drink · case','FOOD','Food & beverages','case',32,24,20,8,'CHILLED',5,0,'LOW','GENERATED_EXAMPLE',12,'LOT-OAT-2601',(today+timedelta(days=20)).isoformat()),
            ('DEMO-ELEC-01','D-ELEC-01','8901000000028','Laptop docking station','ELEC','Electrical & electronics','each',22,15,6,1.1,'DRY_SECURE',4,1,'LOW','GENERATED_EXAMPLE',8,'LOT-DCK-2601',''),
            ('DEMO-CHEM-01','D-CHEM-01','8901000000035','Surface cleaner · sealed carton','CHEM','Chemicals & process materials','case',30,24,28,12,'HAZMAT',2,0,'MEDIUM','GENERATED_EXAMPLE',4,'LOT-CLN-2601',''),
            ('DEMO-FRZ-01','D-FRZ-01','8901000000042','Frozen berry carton','FOOD','Food & beverages','case',40,30,24,9,'FROZEN',4,0,'LOW','GENERATED_EXAMPLE',10,'LOT-BRY-2601',(today+timedelta(days=180)).isoformat()),
            ('DEMO-BATT-01','D-BATT-01','8901000000059','Damaged lithium battery for quarantine','WASTE','Batteries','each',18,12,8,1.8,'DRY_SECURE',1,1,'HIGH','GENERATED_EXAMPLE',1,'LOT-BAT-2601','')]
            for d in demos:
                c.execute('INSERT OR IGNORE INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',d[:16])
                qty=d[16]; lot=d[17]; expiry=d[18]; shelf='WH007-W03' if d[0]=='DEMO-BATT-01' else None
                quarantined=1 if d[0]=='DEMO-BATT-01' else 0; reason='Damaged battery awaiting authorized inspection' if quarantined else None
                c.execute('INSERT OR IGNORE INTO lots(product_id,lot_code,expiry,qty,shelf_id,quarantined,quarantine_reason) VALUES(?,?,?,?,?,?,?)',(d[0],lot,expiry,qty,shelf,quarantined,reason))
    for stream in WASTE_STREAMS:
        if not c.execute('SELECT 1 FROM shelves WHERE warehouse_id=? AND category_codes=?',( 'WH007',stream['category'])).fetchone():
            next_id=c.execute("SELECT count(*) FROM shelves WHERE warehouse_id='WH007'").fetchone()[0]+1
            c.execute('INSERT INTO shelves VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('WH007-W%02d'%next_id,'WH007',stream['category'],stream['category'],stream['storage'],150,150,180,250000,50000,1,0,'Available',int(stream['restricted'])))
    for sh in c.execute('SELECT id FROM shelves').fetchall():
        label='NWLOC'+re.sub('[^A-Za-z0-9]','',sh['id']).upper()
        c.execute('INSERT OR IGNORE INTO shelf_labels(shelf_id,barcode) VALUES(?,?)',(sh['id'],label))
    if seed_users and c.execute('SELECT count(*) FROM users').fetchone()[0]==0:
        c.execute('INSERT INTO users(username,password_hash,role,force_change) VALUES(?,?,?,1)',(DEMO_USER,pw_hash(DEMO_PASSWORD_OVERRIDE or '123'),'manager'))
    if seed_users:
        user=c.execute('SELECT password_hash FROM users WHERE username=?',(DEMO_USER,)).fetchone()
        if user and user['password_hash']==LEGACY_DEMO_PASSWORD_HASH:
            c.execute('UPDATE users SET password_hash=?,force_change=1 WHERE username=?',(pw_hash(DEMO_PASSWORD_OVERRIDE or '123'),DEMO_USER))
    if seed_demo_data:
        ensure_sample_inventory(c)
    c.commit(); c.close()

def ensure_sample_inventory(c):
    """Add clearly labeled, idempotent teaching stock to the requested staff/demo stores."""
    today=date.today()
    samples=[
      ('DEMO-FOOD-01','D-FOOD-01','8901000000011','Organic oat drink · case','FOOD','Food & beverages','case',32,24,20,8,'CHILLED',5,0,'LOW','GENERATED_EXAMPLE',12,'LOT-OAT-2601',(today+timedelta(days=20)).isoformat(),None,0,None),
      ('DEMO-ELEC-01','D-ELEC-01','8901000000028','Laptop docking station','ELEC','Electrical & electronics','each',22,15,6,1.1,'DRY_SECURE',4,1,'LOW','GENERATED_EXAMPLE',8,'LOT-DCK-2601','', 'WH003-A01-B01',0,None),
      ('DEMO-CHEM-01','D-CHEM-01','8901000000035','Surface cleaner · sealed carton','CHEM','Chemicals & process materials','case',30,24,28,12,'HAZMAT',2,0,'MEDIUM','GENERATED_EXAMPLE',4,'LOT-CLN-2601','',None,0,None),
      ('DEMO-FRZ-01','D-FRZ-01','8901000000042','Frozen berry carton','FOOD','Food & beverages','case',40,30,24,9,'FROZEN',4,0,'LOW','GENERATED_EXAMPLE',10,'LOT-BRY-2601',(today+timedelta(days=180)).isoformat(),'WH002-FROZEN-01',0,None),
      ('DEMO-BATT-01','D-BATT-01','8901000000059','Damaged lithium battery for quarantine','WASTE','Batteries','each',18,12,8,1.8,'DRY_SECURE',1,1,'HIGH','GENERATED_EXAMPLE',1,'LOT-BAT-2601','', 'WH007-W03',1,'Damaged battery awaiting authorized inspection')
    ]
    for d in samples:
        c.execute('INSERT OR IGNORE INTO products(id,sku,barcode,name,category_code,category,uom,length,width,height,weight,storage,priority,fragile,hazard,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',d[:16])
        existing=c.execute('SELECT id FROM lots WHERE product_id=? AND lot_code=? ORDER BY id LIMIT 1',(d[0],d[17])).fetchone()
        if not existing:
            cur=c.execute('INSERT INTO lots(product_id,lot_code,expiry,qty,shelf_id,quarantined,quarantine_reason,disposition,waste_category) VALUES(?,?,?,?,?,?,?,?,?)',(d[0],d[17],d[18] or None,d[16],d[19],d[20],d[21],'WASTE' if d[4]=='WASTE' else 'USEFUL',d[5] if d[4]=='WASTE' else None))
            lot_id=cur.lastrowid
        else:
            lot_id=existing['id']
            c.execute('UPDATE lots SET disposition=?,waste_category=? WHERE id=?',('WASTE' if d[4]=='WASTE' else 'USEFUL',d[5] if d[4]=='WASTE' else None,lot_id))
        if d[19]:
            base_ref='SAMPLE-SEED-'+d[0]
            existing_seed=c.execute('SELECT id FROM movements WHERE reference=? LIMIT 1',(base_ref,)).fetchone()
            if not existing_seed:
                c.execute('UPDATE lots SET shelf_id=? WHERE id=?',(d[19],lot_id))
            now=datetime.utcnow().isoformat(timespec='seconds')+'Z'
            if d[20]:
                # Quarantined examples enter quarantine directly; they are never shown as normal putaway.
                if existing_seed:
                    c.execute("UPDATE movements SET kind='QUARANTINE',dest_shelf=?,reason='Generated quarantined sample; not a live warehouse movement.' WHERE reference=?",(d[19],base_ref))
                    c.execute('DELETE FROM movements WHERE reference=?',(base_ref+'-PUTAWAY',))
                else:
                    c.execute('INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at,status,reference) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('QUARANTINE',d[0],d[17],d[16],None,d[19],'Generated quarantined sample; not a live warehouse movement.','SAMPLE DATA',now,'COMPLETED',base_ref))
            else:
                if not existing_seed:
                    c.execute('INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at,status,reference) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('RECEIVING',d[0],d[17],d[16],None,None,'Generated teaching sample; not a live warehouse receipt.','SAMPLE DATA',now,'COMPLETED',base_ref))
                putaway_ref=base_ref+'-PUTAWAY'
                if not c.execute('SELECT 1 FROM movements WHERE reference=? LIMIT 1',(putaway_ref,)).fetchone():
                    c.execute('INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at,status,reference) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('PUTAWAY',d[0],d[17],d[16],None,d[19],'Generated teaching sample placement; not a live warehouse movement.','SAMPLE DATA',now,'COMPLETED',putaway_ref))

    bottle=('DEMO-PLAST-01','PL-BTL-01','8908006727421','Plastic bottle · reusable sample','PACK','Plastic packaging','each',6.5,6.5,22,0.03,'AMBIENT',2,0,'LOW','GENERATED_EXAMPLE')
    c.execute('INSERT OR IGNORE INTO products(id,sku,barcode,name,category_code,category,uom,length,width,height,weight,storage,priority,fragile,hazard,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',bottle)
    if not c.execute('SELECT 1 FROM lots WHERE product_id=? AND lot_code=?',(bottle[0],'LOT-BTL-DEMO')).fetchone():
        c.execute("INSERT INTO lots(product_id,lot_code,qty,disposition) VALUES(?,?,1,'USEFUL')",(bottle[0],'LOT-BTL-DEMO'))
        now=datetime.utcnow().isoformat(timespec='seconds')+'Z'
        c.execute('INSERT INTO movements(kind,product_id,lot_code,qty,reason,actor,created_at,status,reference) VALUES(?,?,?,?,?,?,?,?,?)',('RECEIVING',bottle[0],'LOT-BTL-DEMO',1,'Generated reusable plastic bottle sample; not a live receipt.','SAMPLE DATA',now,'COMPLETED','SAMPLE-SEED-PLASTIC-BOTTLE'))

def remove_legacy_demo_fixtures():
    fixtures={'DEMO-FOOD-01':'D-FOOD-01','DEMO-ELEC-01':'D-ELEC-01','DEMO-CHEM-01':'D-CHEM-01','DEMO-FRZ-01':'D-FRZ-01','DEMO-BATT-01':'D-BATT-01'}
    c=connect()
    for product_id,sku in fixtures.items():
        product=c.execute('SELECT id FROM products WHERE id=? AND sku=? AND source="GENERATED_EXAMPLE"',(product_id,sku)).fetchone()
        if not product: continue
        if c.execute('SELECT 1 FROM movements WHERE product_id=? LIMIT 1',(product_id,)).fetchone(): continue
        if c.execute('SELECT 1 FROM audit WHERE entity=? LIMIT 1',(product_id,)).fetchone(): continue
        c.execute('DELETE FROM lots WHERE product_id=?',(product_id,))
        c.execute('DELETE FROM products WHERE id=?',(product_id,))
    c.commit(); c.close()

def audit(c,actor,action,entity,details):
    c.execute('INSERT INTO audit(actor,action,entity,details,created_at) VALUES(?,?,?,?,?)',(actor,action,entity,json.dumps(details,ensure_ascii=False),datetime.utcnow().isoformat(timespec='seconds')+'Z'))
def rowdict(r): return dict(r) if r else None
def request_db(handler):
    session=session_for(handler)
    return connect(DEMO_DB_PATH if session and session['kind']=='demo' else DB_PATH)
def session_for(handler):
    cookie=handler.headers.get('Cookie','')
    sid=''
    for part in cookie.split(';'):
        if part.strip().startswith('nw_session='): sid=part.strip().split('=',1)[1]
    s=SESSIONS.get(sid)
    if s and s['expires']>time.time(): return s
    if sid: SESSIONS.pop(sid,None)
    return None
def new_session(data):
    sid=secrets.token_urlsafe(32); expiry=time.time()+SESSION_TTL; SESSIONS[sid]=dict(data,expires=expiry,expires_at=int(expiry)); return sid

def shelf_load(c,shelf_id,exclude_transfer=None):
    used=c.execute('SELECT coalesce(sum(p.length*p.width*p.height*(l.qty-l.reserved)/1000.0),0) vl,coalesce(sum(p.weight*(l.qty-l.reserved)),0) kg FROM lots l JOIN products p ON p.id=l.product_id WHERE l.shelf_id=?',(shelf_id,)).fetchone()
    sql="SELECT coalesce(sum(p.length*p.width*p.height*t.qty/1000.0),0) vl,coalesce(sum(p.weight*t.qty),0) kg FROM transfer_orders t JOIN products p ON p.id=t.product_id WHERE t.dest_shelf=? AND t.state='IN_TRANSIT'"
    args=[shelf_id]
    if exclude_transfer:
        sql+=' AND t.id<>?'; args.append(exclude_transfer)
    inbound=c.execute(sql,args).fetchone()
    return float(used['vl'])+float(inbound['vl']),float(used['kg'])+float(inbound['kg'])

def validate_destination(c,product,dest,qty,role,exclude_transfer=None):
    sh=c.execute('SELECT * FROM shelves WHERE id=? AND status="Available"',(dest,)).fetchone()
    if not sh: raise ValueError('Destination shelf is unavailable.')
    if not location_allowed(sh,product['category'],role): raise ValueError('Configured waste-stream permissions do not authorize this movement.')
    if sh['warehouse_id']=='WH007' and (product['category_code']!='WASTE' or waste_stream(product['category']) is None): raise ValueError('Product category is not a configured waste stream.')
    if sh['warehouse_id']!='WH007' and product['category_code']=='WASTE': raise ValueError('Waste stock must stay in the Waste warehouse.')
    coverage=sh['category_codes'] or 'ALL'
    if coverage!='ALL' and product['category_code'] not in coverage.split(',') and product['category'] not in coverage.split(','): raise ValueError('Destination shelf category coverage does not match.')
    if product['storage']!=sh['storage']: raise ValueError('Storage profile mismatch.')
    if product['length']>sh['max_length'] or product['width']>sh['max_width'] or product['height']>sh['max_height']: raise ValueError('Product dimensions exceed shelf limits.')
    used_l,used_kg=shelf_load(c,dest,exclude_transfer)
    if product['length']*product['width']*product['height']*qty/1000>sh['capacity_l']-used_l or product['weight']*qty>sh['max_weight']-used_kg: raise ValueError('Destination shelf lacks sufficient remaining capacity.')
    return sh

def allocation_suggestions(c, lot, role):
    candidates=[]
    shelves=c.execute('SELECT sh.*,w.name warehouse_name FROM shelves sh JOIN warehouses w ON w.id=sh.warehouse_id WHERE sh.status="Available"').fetchall()
    for sh in shelves:
        reasons=[]
        if sh['storage'] not in (lot['storage'],'AMBIENT') or (lot['storage'] in ('CHILLED','FROZEN','HAZMAT','DRY_SECURE','CONTROLLED','WASTE') and sh['storage']!=lot['storage']): reasons.append('storage profile mismatch')
        if sh['warehouse_id']=='WH007' and (lot['category_code']!='WASTE' or lot['category'] not in WASTE_CATEGORIES): reasons.append('not a designated waste stream')
        if sh['warehouse_id']!='WH007' and lot['category_code']=='WASTE': reasons.append('waste must stay in Waste warehouse')
        cov=sh['category_codes'] or 'ALL'
        if cov!='ALL' and lot['category_code'] not in cov.split(',') and lot['category'] not in cov.split(','): reasons.append('category not covered')
        if lot['length']>sh['max_length'] or lot['width']>sh['max_width'] or lot['height']>sh['max_height']: reasons.append('item exceeds shelf dimensions')
        used_l,used_kg=shelf_load(c,sh['id'])
        add_l=float(lot['length'])*float(lot['width'])*float(lot['height'])*int(lot['qty'])/1000.0
        add_kg=float(lot['weight'])*int(lot['qty'])
        if add_l>float(sh['capacity_l'])-used_l: reasons.append('insufficient remaining volume')
        if add_kg>float(sh['max_weight'])-used_kg: reasons.append('insufficient remaining weight')
        if not location_allowed(sh,lot['category'],role): reasons.append('restricted location requires an authorized role')
        if reasons: continue
        usedpct=(used_l+add_l)/float(sh['capacity_l']) if sh['capacity_l'] else 1
        accessible=(int(sh['access_easy']) and int(sh['level'])<=2)
        fill_after=1-usedpct
        score=(35 if int(lot['priority'])>=4 and accessible else 0)+int(fill_after*24)+int((used_kg+add_kg)/float(sh['max_weight'])*8)
        candidates.append({'shelf_id':sh['id'],'warehouse_id':sh['warehouse_id'],'warehouse_name':sh['warehouse_name'],'zone':sh['zone'],'level':sh['level'],'easy_access':bool(sh['access_easy']),'remaining_l':round(float(sh['capacity_l'])-used_l-add_l,1),'remaining_kg':round(float(sh['max_weight'])-used_kg-add_kg,1),'score':score,'reasons':['storage compatible','category compatible','dimensions fit','volume and weight capacity available']+(['easy-access low shelf for frequent picking'] if int(lot['priority'])>=4 and accessible else [])})
    candidates.sort(key=lambda x:(-x['score'],x['remaining_l']))
    return candidates

def effective_product(product, lot):
    result=dict(product)
    if lot['disposition']=='WASTE':
        stream=waste_stream(lot['waste_category'] or result.get('category'))
        if not stream: raise ValueError('Choose a configured waste stream for this lot before allocating it.')
        result.update(category_code='WASTE',category=stream['category'],storage=stream['storage'])
    return result

class App(BaseHTTPRequestHandler):
    server_version='SmartSlots/1.0'
    def log_message(self,fmt,*args): pass
    def send_json(self,obj,status=200,headers=None):
        b=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n).decode() or '{}')
    def fail(self,message,status=400): self.send_json({'error':message},status)
    def actor(self,required=True):
        s=session_for(self)
        if not s and required: return None
        return s
    def do_GET(self):
        path=urllib.parse.urlparse(self.path).path
        if path in ('/','/index.html'):
            cookie=self.headers.get('Cookie','')
            for part in cookie.split(';'):
                if part.strip().startswith('nw_session='):
                    sid=part.strip().split('=',1)[1]; active=SESSIONS.pop(sid,None)
                    if active and active.get('kind')=='staff':
                        c=connect(); audit(c,active['user'],'SESSION_RESTARTED','session',{'reason':'browser page reload restarted BIOS and sign-in'}); c.commit(); c.close()
            try: b=(HERE/'norware_web.html').read_bytes()
            except OSError: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store'); self.send_header('Set-Cookie','nw_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'); self.end_headers(); self.wfile.write(b); return
        if path=='/api/session':
            s=session_for(self); self.send_json({'session':({k:v for k,v in s.items() if k not in ('expires',)} if s else None)}); return
        s=self.actor()
        if not s: self.fail('Sign in or start a local demo session first.',401); return
        c=request_db(self)
        if path=='/api/bootstrap':
            wh=[rowdict(x) for x in c.execute('SELECT w.*,count(sh.id) location_count FROM warehouses w LEFT JOIN shelves sh ON sh.warehouse_id=w.id GROUP BY w.id ORDER BY w.id')]
            shelves=[rowdict(x) for x in c.execute('''SELECT sh.*,w.name warehouse_name,lab.barcode shelf_barcode,coalesce(sum(l.qty-l.reserved),0) stock_units,coalesce(sum(p.length*p.width*p.height*(l.qty-l.reserved)/1000.0),0)+coalesce((SELECT sum(ip.length*ip.width*ip.height*t.qty/1000.0) FROM transfer_orders t JOIN products ip ON ip.id=t.product_id WHERE t.dest_shelf=sh.id AND t.state='IN_TRANSIT'),0) used_l,coalesce(sum(p.weight*(l.qty-l.reserved)),0)+coalesce((SELECT sum(ip.weight*t.qty) FROM transfer_orders t JOIN products ip ON ip.id=t.product_id WHERE t.dest_shelf=sh.id AND t.state='IN_TRANSIT'),0) used_kg FROM shelves sh JOIN warehouses w ON w.id=sh.warehouse_id LEFT JOIN shelf_labels lab ON lab.shelf_id=sh.id LEFT JOIN lots l ON l.shelf_id=sh.id LEFT JOIN products p ON p.id=l.product_id GROUP BY sh.id ORDER BY sh.warehouse_id,sh.id''')]
            products=[rowdict(x) for x in c.execute('''SELECT p.id,p.sku,p.barcode,p.name,p.category_code,p.category,p.uom,p.length,p.width,p.height,p.weight,p.storage,p.priority,p.fragile,p.hazard,p.source,coalesce(sum(l.qty),0) stock,coalesce(sum(CASE WHEN l.quarantined=1 THEN l.qty ELSE 0 END),0) quarantined FROM products p LEFT JOIN lots l ON l.product_id=p.id GROUP BY p.id ORDER BY CASE WHEN p.id LIKE 'DEMO-%' THEN 0 ELSE 1 END,p.name LIMIT 160''')]
            lots=[rowdict(x) for x in c.execute('''SELECT l.*,p.name product_name,p.barcode,p.storage,p.category,s.warehouse_id,s.id current_shelf FROM lots l JOIN products p ON p.id=l.product_id LEFT JOIN shelves s ON s.id=l.shelf_id ORDER BY l.id DESC LIMIT 250''')]
            moves=[rowdict(x) for x in c.execute('SELECT * FROM movements ORDER BY id DESC LIMIT 500')]
            transfers=[rowdict(x) for x in c.execute("SELECT * FROM transfer_orders WHERE state='IN_TRANSIT' ORDER BY created_at DESC")]
            audit_rows=[rowdict(x) for x in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 40')]
            product_count=c.execute('SELECT count(*) FROM products').fetchone()[0]
            c.close(); self.send_json({'session':{k:v for k,v in s.items() if k!='expires'},'warehouses':wh,'shelves':shelves,'products':products,'product_count':product_count,'lots':lots,'movements':moves,'transfers':transfers,'audit':audit_rows,'waste_categories':WASTE_CATEGORIES}); return
        if path=='/api/search':
            q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('q',[''])[0]
            rows=[rowdict(x) for x in c.execute('''SELECT p.*,coalesce(sum(l.qty),0) stock,coalesce(sum(CASE WHEN l.quarantined=1 THEN l.qty ELSE 0 END),0) quarantined FROM products p LEFT JOIN lots l ON l.product_id=p.id WHERE p.barcode=? OR p.sku=? OR p.id=? OR p.name LIKE ? OR p.category LIKE ? GROUP BY p.id ORDER BY CASE WHEN p.id LIKE 'DEMO-%' THEN 0 ELSE 1 END,p.name LIMIT 30''',(q,q,q,'%'+q+'%','%'+q+'%'))]
            shelf=[rowdict(x) for x in c.execute('SELECT s.*,l.barcode shelf_barcode FROM shelves s LEFT JOIN shelf_labels l ON l.shelf_id=s.id WHERE s.id=? OR l.barcode=?',(q,q))]; c.close(); self.send_json({'products':rows,'shelves':shelf}); return
        if path=='/api/audit':
            rows=[rowdict(x) for x in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')]; c.close(); self.send_json({'audit':rows}); return
        c.close(); self.fail('Not found.',404)
    def do_POST(self):
        path=urllib.parse.urlparse(self.path).path
        try: data=self.body()
        except Exception: self.fail('Invalid request body.'); return
        if path=='/api/entry/demo':
            sid=new_session({'kind':'demo','user':'Local Demo','role':'demo','demo_id':'DEMO-'+secrets.token_hex(3).upper()})
            self.send_json({'session':{k:v for k,v in SESSIONS[sid].items() if k!='expires'}},200,{'Set-Cookie':'nw_session='+sid+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=1800'}); return
        if path=='/api/login':
            username=str(data.get('username','')); password=str(data.get('password','')); c=connect(); user=c.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone()
            if not user or not pw_check(password,user['password_hash']):
                if user: audit(c,username,'LOGIN_FAILED','user',{})
                c.commit(); c.close(); self.fail('Username or password is incorrect.',401); return
            audit(c,username,'LOGIN','user',{'role':user['role']}); c.commit(); force=bool(user['force_change']); c.close()
            sid=new_session({'kind':'staff','user':username,'role':user['role'],'force_change':force})
            self.send_json({'session':{k:v for k,v in SESSIONS[sid].items() if k!='expires'}},200,{'Set-Cookie':'nw_session='+sid+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=1800'}); return
        if path=='/api/logout':
            s=session_for(self); cookie=self.headers.get('Cookie',''); sid=''
            for x in cookie.split(';'):
                if x.strip().startswith('nw_session='): sid=x.strip().split('=',1)[1]
            if s:
                SESSIONS.pop(sid,None)
                if s.get('kind')=='staff':
                    c=connect(); audit(c,s['user'],'LOGOUT','session',{'reason':'staff exit'}); c.commit(); c.close()
            self.send_json({'ok':True},200,{'Set-Cookie':'nw_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'}); return
        s=self.actor()
        if not s: self.fail('Sign in or start a local demo session first.',401); return
        c=request_db(self); actor=s['user']; role=s['role']
        if s.get('force_change') and path!='/api/password': c.close(); self.fail('Change the temporary staff password before continuing.',403); return
        try:
            if path=='/api/password':
                if s['kind']!='staff': raise ValueError('Staff login is required to change a password.')
                pw=str(data.get('password',''))
                if len(pw)<12: raise ValueError('Use at least 12 characters for the new password.')
                c.execute('UPDATE users SET password_hash=?,force_change=0 WHERE username=?',(pw_hash(pw),actor)); audit(c,actor,'PASSWORD_CHANGED','user',{}); c.commit(); s['force_change']=False; c.close(); self.send_json({'ok':True}); return
            if path=='/api/lot/disposition':
                if s['kind'] not in ('staff','demo'): raise ValueError('A NORWARE session is required to change product condition.')
                lot=c.execute('SELECT * FROM lots WHERE id=?',(int(data['lot_id']),)).fetchone()
                if not lot: raise ValueError('Product lot not found.')
                disposition=str(data.get('disposition','')).upper()
                if disposition not in ('USEFUL','WASTE'): raise ValueError('Choose whether the product is still useful or is waste.')
                stream_name=None
                if disposition=='WASTE':
                    stream=waste_stream(data.get('waste_category'))
                    if not stream: raise ValueError('Choose a configured waste stream for this lot.')
                    if (role if s['kind']=='staff' else 'operator') not in stream['roles']: raise ValueError('Your role is not authorized for this waste stream.')
                    stream_name=stream['category']
                if c.execute("SELECT 1 FROM transfer_orders WHERE lot_id=? AND state='IN_TRANSIT' LIMIT 1",(lot['id'],)).fetchone():
                    raise ValueError('Receive or cancel the in-transit lot before changing its condition.')
                previous={'disposition':lot['disposition'],'waste_category':lot['waste_category']}
                c.execute('UPDATE lots SET disposition=?,waste_category=? WHERE id=?',(disposition,stream_name,lot['id']))
                audit(c,actor,'LOT_DISPOSITION_CHANGED',lot['product_id'],{'lot':lot['lot_code'],'quantity':lot['qty'],'from':previous,'to':{'disposition':disposition,'waste_category':stream_name},'current_shelf':lot['shelf_id']})
                c.execute('INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,reason,actor,created_at,status) VALUES(?,?,?,?,?,?,?,?,?)',('DISPOSITION_CHANGE',lot['product_id'],lot['lot_code'],lot['qty'],lot['shelf_id'],'Lot condition changed to '+disposition+(' · '+stream_name if stream_name else ''),actor,datetime.utcnow().isoformat(timespec='seconds')+'Z','COMPLETED'))
                c.commit(); c.close(); self.send_json({'ok':True}); return
            if path=='/api/products':
                p=data; required=['id','sku','barcode','name','category','length','width','height','weight','storage','priority','quantity','lot']
                if any(p.get(k) in (None,'') for k in required): raise ValueError('Complete all required product and lot fields.')
                disposition=str(p.get('disposition','USEFUL')).upper()
                if disposition not in ('USEFUL','WASTE'): raise ValueError('Choose whether the product is still useful or is waste.')
                waste_category=p.get('waste_category')
                if disposition=='WASTE':
                    stream=waste_stream(waste_category)
                    if not stream: raise ValueError('Choose a configured waste stream for this product.')
                    category_code='WASTE'; category_name=waste_category; storage=stream['storage']
                else:
                    if p.get('category')=='WASTE': raise ValueError('Choose Waste as the product condition and select a waste stream.')
                    category_code=p['category']; category_name=p.get('category_name',p['category']); storage=p['storage']
                if c.execute('SELECT 1 FROM products WHERE id=? OR sku=? OR barcode=?',(p['id'],p['sku'],p['barcode'])).fetchone(): raise ValueError('Product ID, SKU, or barcode already exists.')
                c.execute('INSERT INTO products(id,sku,barcode,name,category_code,category,uom,length,width,height,weight,storage,priority,fragile,hazard) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(p['id'],p['sku'],p['barcode'],p['name'],category_code,category_name,p.get('uom','each'),float(p['length']),float(p['width']),float(p['height']),float(p['weight']),storage,int(p['priority']),int(bool(p.get('fragile'))),p.get('hazard','LOW')))
                lot_cursor=c.execute('INSERT INTO lots(product_id,lot_code,expiry,qty,quarantined,quarantine_reason,disposition,waste_category) VALUES(?,?,?,?,?,?,?,?)',(p['id'],p['lot'],p.get('expiry') or None,int(p['quantity']),int(bool(p.get('quarantined'))),p.get('quarantine_reason'),disposition,waste_category if disposition=='WASTE' else None))
                movement_kind='QUARANTINE' if p.get('quarantined') else 'RECEIVING'; reason=p.get('quarantine_reason') or 'Product received into unallocated stock'
                c.execute('INSERT INTO movements(kind,product_id,lot_code,qty,reason,actor,created_at) VALUES(?,?,?,?,?,?,?)',(movement_kind,p['id'],p['lot'],int(p['quantity']),reason,actor,datetime.utcnow().isoformat(timespec='seconds')+'Z'))
                audit(c,actor,'PRODUCT_RECEIVED',p['id'],{'quantity':int(p['quantity']),'lot':p['lot'],'quarantined':bool(p.get('quarantined')),'disposition':disposition}); c.commit(); c.close(); self.send_json({'ok':True,'lot_id':lot_cursor.lastrowid}); return
            if path=='/api/suggest':
                pid=data['product_id']; lot=(c.execute('SELECT l.*,p.* FROM lots l JOIN products p ON p.id=l.product_id WHERE l.product_id=? AND l.id=?',(pid,int(data['lot_id']))).fetchone() if data.get('lot_id') else c.execute('SELECT l.*,p.* FROM lots l JOIN products p ON p.id=l.product_id WHERE l.product_id=? AND l.lot_code=? ORDER BY l.id LIMIT 1',(pid,data.get('lot',''))).fetchone())
                if not lot: raise ValueError('Product lot not found.')
                if lot['quarantined']: raise ValueError('Quarantined stock cannot be allocated normally.')
                if lot['expiry'] and lot['expiry']<=date.today().isoformat(): raise ValueError('Expired stock cannot be allocated. Review authorized disposition.')
                lot=effective_product(lot,lot)
                candidates=allocation_suggestions(c,lot,role)
                out={'suggestions':candidates[:8],'eligible_count':len(candidates),'message':None if candidates else 'No shelf passes storage, category, dimensions, capacity, availability, and access rules.'}
                c.close(); self.send_json(out); return
            if path=='/api/allocate':
                if s['kind']=='demo': raise ValueError('Local demo sessions cannot confirm stock placement.')
                pid=data['product_id']; lotcode=data['lot']; dest=data['shelf_id']; lot=(c.execute('SELECT l.*,p.* FROM lots l JOIN products p ON p.id=l.product_id WHERE l.product_id=? AND l.id=?',(pid,int(data['lot_id']))).fetchone() if data.get('lot_id') else c.execute('SELECT l.*,p.* FROM lots l JOIN products p ON p.id=l.product_id WHERE l.product_id=? AND l.lot_code=? ORDER BY l.id LIMIT 1',(pid,lotcode)).fetchone())
                if not lot or lot['quarantined']: raise ValueError('Lot missing or quarantined.')
                lot=effective_product(lot,lot)
                sh=c.execute('SELECT * FROM shelves WHERE id=? AND status="Available"',(dest,)).fetchone()
                if not sh: raise ValueError('Shelf is unavailable; refresh and choose another location.')
                # Repeat suitability and capacity validation immediately before confirmation.
                valid_storage=sh['storage']==lot['storage']
                if not valid_storage: raise ValueError('Storage requirement no longer matches this shelf.')
                if sh['warehouse_id']=='WH007' and (lot['category_code']!='WASTE' or lot['category'] not in WASTE_CATEGORIES): raise ValueError('Product is not assigned to a configured waste stream.')
                if sh['warehouse_id']!='WH007' and lot['category_code']=='WASTE': raise ValueError('Waste stock must stay in the Waste warehouse.')
                cov=sh['category_codes'] or 'ALL'
                if cov!='ALL' and lot['category_code'] not in cov.split(',') and lot['category'] not in cov.split(','): raise ValueError('Shelf category coverage does not match.')
                if lot['length']>sh['max_length'] or lot['width']>sh['max_width'] or lot['height']>sh['max_height']: raise ValueError('Product dimensions exceed shelf limits.')
                used_l,used_kg=shelf_load(c,dest)
                if lot['length']*lot['width']*lot['height']*lot['qty']/1000>sh['capacity_l']-used_l or lot['weight']*lot['qty']>sh['max_weight']-used_kg: raise ValueError('Shelf capacity changed; request a fresh suggestion.')
                if not location_allowed(sh,lot['category'],role): raise ValueError('Configured waste-stream permissions do not authorize this placement.')
                source=lot['shelf_id']
                c.execute('UPDATE lots SET shelf_id=? WHERE id=?',(dest,lot['id']))
                c.execute('INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?)',('PUTAWAY',pid,lotcode,lot['qty'],source,dest,'Staff-confirmed allocation',actor,datetime.utcnow().isoformat(timespec='seconds')+'Z'))
                audit(c,actor,'ALLOCATION_CONFIRMED',pid,{'lot':lotcode,'quantity':lot['qty'],'source':source,'destination':dest})
                c.commit(); c.close(); self.send_json({'ok':True}); return
            if path=='/api/transfer/start':
                lot=c.execute('SELECT * FROM lots WHERE product_id=? AND id=?',(data['product_id'],int(data['lot_id']))).fetchone()
                if not lot or lot['quarantined']: raise ValueError('Choose a valid, non-quarantined lot.')
                qty=int(data.get('qty',0)); source=lot['shelf_id']; dest=str(data.get('dest_shelf') or '')
                if qty<=0 or qty>int(lot['qty'])-int(lot['reserved']): raise ValueError('Transfer quantity must be within available lot stock.')
                if not source: raise ValueError('This lot is not on a shelf. Use receiving or putaway instead of transfer.')
                if source==dest: raise ValueError('Choose a different destination shelf.')
                if lot['expiry'] and lot['expiry']<=date.today().isoformat(): raise ValueError('Expired stock cannot be transferred as usable stock.')
                product=c.execute('SELECT * FROM products WHERE id=?',(lot['product_id'],)).fetchone()
                validate_destination(c,effective_product(product,lot),dest,qty,role)
                now=datetime.utcnow().isoformat(timespec='seconds')+'Z'; ref=str(data.get('reference') or '').strip(); transfer_id='TR-'+secrets.token_hex(5).upper()
                if qty==int(lot['qty']):
                    c.execute('UPDATE lots SET shelf_id=NULL WHERE id=?',(lot['id'],)); transit_lot_id=lot['id']
                else:
                    c.execute('UPDATE lots SET qty=qty-? WHERE id=?',(qty,lot['id']))
                    cur=c.execute('INSERT INTO lots(product_id,lot_code,expiry,qty,shelf_id,quarantined,disposition,waste_category) VALUES(?,?,?,?,NULL,0,?,?)',(lot['product_id'],lot['lot_code'],lot['expiry'],qty,lot['disposition'],lot['waste_category'])); transit_lot_id=cur.lastrowid
                cur=c.execute("INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at,status,reference) VALUES('TRANSFER',?,?,?,?,?,?,?,?,?,?)",(lot['product_id'],lot['lot_code'],qty,source,dest,data.get('reason','Transfer started'),actor,now,'IN_TRANSIT',ref))
                c.execute('INSERT INTO transfer_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(transfer_id,cur.lastrowid,transit_lot_id,lot['product_id'],lot['lot_code'],qty,source,dest,ref,'IN_TRANSIT',now,now))
                audit(c,actor,'TRANSFER_STARTED',transfer_id,{'product':lot['product_id'],'lot':lot['lot_code'],'quantity':qty,'source':source,'destination':dest,'reference':ref})
                c.commit(); c.close(); self.send_json({'ok':True,'transfer_id':transfer_id}); return
            if path=='/api/transfer/receive':
                transfer=c.execute("SELECT * FROM transfer_orders WHERE id=? AND state='IN_TRANSIT'",(data['transfer_id'],)).fetchone()
                if not transfer: raise ValueError('Transfer is not awaiting receipt.')
                if str(data.get('shelf_id') or '')!=transfer['dest_shelf']: raise ValueError('Scan or enter the assigned destination shelf before confirming receipt.')
                lot=c.execute('SELECT * FROM lots WHERE id=?',(transfer['lot_id'],)).fetchone()
                if not lot: raise ValueError('In-transit lot record is missing; contact a manager.')
                product=c.execute('SELECT * FROM products WHERE id=?',(transfer['product_id'],)).fetchone()
                validate_destination(c,effective_product(product,lot),transfer['dest_shelf'],transfer['qty'],role,exclude_transfer=transfer['id'])
                now=datetime.utcnow().isoformat(timespec='seconds')+'Z'
                c.execute('UPDATE lots SET shelf_id=? WHERE id=?',(transfer['dest_shelf'],lot['id']))
                c.execute("UPDATE transfer_orders SET state='RECEIVED',updated_at=? WHERE id=?",(now,transfer['id']))
                c.execute("UPDATE movements SET status='COMPLETED' WHERE id=?",(transfer['movement_id'],))
                c.execute("INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at,status,reference) VALUES('TRANSFER_RECEIPT',?,?,?,?,?,?,?,?,?,?)",(transfer['product_id'],transfer['lot_code'],transfer['qty'],transfer['source_shelf'],transfer['dest_shelf'],data.get('reason','Destination receipt confirmed'),actor,now,'COMPLETED',transfer['reference']))
                audit(c,actor,'TRANSFER_RECEIVED',transfer['id'],{'quantity':transfer['qty'],'destination':transfer['dest_shelf'],'reference':transfer['reference']})
                c.commit(); c.close(); self.send_json({'ok':True}); return
            if path=='/api/transfer/cancel':
                transfer=c.execute("SELECT * FROM transfer_orders WHERE id=? AND state='IN_TRANSIT'",(data['transfer_id'],)).fetchone()
                if not transfer: raise ValueError('Transfer is not awaiting receipt.')
                now=datetime.utcnow().isoformat(timespec='seconds')+'Z'
                c.execute("UPDATE transfer_orders SET state='CANCELLED',updated_at=? WHERE id=?",(now,transfer['id']))
                c.execute("UPDATE movements SET status='CANCELLED' WHERE id=?",(transfer['movement_id'],))
                audit(c,actor,'TRANSFER_CANCELLED',transfer['id'],{'quantity':transfer['qty'],'source':transfer['source_shelf'],'stock':'returned to unallocated stock'})
                c.commit(); c.close(); self.send_json({'ok':True}); return
            if path=='/api/movement':
                kind=data.get('kind'); allowed=['RECEIVING','PUTAWAY','TRANSFER','RELOCATION','DISPATCH','ADJUSTMENT','QUARANTINE','QUARANTINE_RELEASE','DISPOSAL']
                if kind not in allowed: raise ValueError('Choose a valid movement type.')
                if kind in ('TRANSFER','RELOCATION'): raise ValueError('Use the guided transfer and receipt workflow for shelf-to-shelf movement.')
                if kind in ('QUARANTINE_RELEASE','DISPOSAL') and role not in ('manager','admin'): raise ValueError('Manager role required for quarantine release or disposal.')
                pid=data['product_id']; lotcode=data['lot']; qty=int(data['qty']); lot=(c.execute('SELECT * FROM lots WHERE product_id=? AND id=?',(pid,int(data['lot_id']))).fetchone() if data.get('lot_id') else c.execute('SELECT * FROM lots WHERE product_id=? AND lot_code=? ORDER BY id LIMIT 1',(pid,lotcode)).fetchone())
                if not lot or qty<=0: raise ValueError('Valid product lot and positive quantity are required.')
                source=data.get('source_shelf') or lot['shelf_id']; dest=data.get('dest_shelf') or (lot['shelf_id'] if kind=='RECEIVING' else None)
                if data.get('source_shelf') and lot['shelf_id'] and data['source_shelf']!=lot['shelf_id']: raise ValueError('Selected source shelf does not match this lot record.')
                if lot['quarantined'] and kind not in ('QUARANTINE_RELEASE','DISPOSAL'): raise ValueError('Quarantined stock must be released by an authorized manager before normal movement.')
                if lot['expiry'] and lot['expiry']<=date.today().isoformat() and kind in ('DISPATCH','TRANSFER','RELOCATION'): raise ValueError('Expired stock cannot be dispatched or relocated as usable stock; record an authorized disposal.')
                if kind in ('DISPATCH','TRANSFER','RELOCATION','QUARANTINE','DISPOSAL') and qty>int(lot['qty'])-int(lot['reserved']): raise ValueError('Quantity exceeds available lot stock.')
                if kind in ('PUTAWAY','TRANSFER','RELOCATION','RECEIVING') and dest:
                    sh=c.execute('SELECT * FROM shelves WHERE id=? AND status="Available"',(dest,)).fetchone()
                    if not sh: raise ValueError('Destination shelf is unavailable.')
                    prod=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
                    prod=effective_product(prod,lot)
                    if not location_allowed(sh,prod['category'],role): raise ValueError('Configured waste-stream permissions do not authorize this movement.')
                    if sh['warehouse_id']=='WH007' and (prod['category_code']!='WASTE' or waste_stream(prod['category']) is None): raise ValueError('Product category is not a configured waste stream.')
                    if sh['warehouse_id']!='WH007' and prod['category_code']=='WASTE': raise ValueError('Waste stock must stay in the Waste warehouse.')
                    coverage=sh['category_codes'] or 'ALL'
                    if coverage!='ALL' and prod['category_code'] not in coverage.split(',') and prod['category'] not in coverage.split(','): raise ValueError('Destination shelf category coverage does not match.')
                    if prod['storage']!=sh['storage'] and not (prod['storage']=='AMBIENT' and sh['storage']=='AMBIENT'): raise ValueError('Storage profile mismatch.')
                    if prod['length']>sh['max_length'] or prod['width']>sh['max_width'] or prod['height']>sh['max_height']: raise ValueError('Product dimensions exceed shelf limits.')
                    used_l,used_kg=shelf_load(c,dest)
                    incoming=qty if dest!=source or kind=='RECEIVING' else 0
                    if prod['length']*prod['width']*prod['height']*incoming/1000>sh['capacity_l']-used_l or prod['weight']*incoming>sh['max_weight']-used_kg: raise ValueError('Destination shelf lacks sufficient remaining capacity.')
                if kind in ('DISPATCH','TRANSFER','RELOCATION','QUARANTINE','DISPOSAL'):
                    if kind=='QUARANTINE': c.execute('UPDATE lots SET quarantined=1,quarantine_reason=? WHERE id=?',(data.get('reason','Quarantine pending review'),lot['id']))
                    elif kind=='QUARANTINE_RELEASE': c.execute('UPDATE lots SET quarantined=0,quarantine_reason=NULL WHERE id=?',(lot['id'],))
                    elif kind=='DISPOSAL': c.execute('UPDATE lots SET qty=qty-? WHERE id=?',(qty,lot['id']))
                    else:
                        if kind=='DISPATCH': c.execute('UPDATE lots SET qty=qty-? WHERE id=?',(qty,lot['id']))
                        elif kind in ('TRANSFER','RELOCATION'):
                            if dest==source: raise ValueError('Choose a different destination shelf.')
                            remain=int(lot['qty'])-qty
                            if remain: c.execute('UPDATE lots SET qty=? WHERE id=?',(remain,lot['id']))
                            else: c.execute('UPDATE lots SET qty=0,shelf_id=NULL WHERE id=?',(lot['id'],))
                            c.execute('INSERT INTO lots(product_id,lot_code,expiry,qty,shelf_id,quarantined,disposition,waste_category) VALUES(?,?,?,?,?,0,?,?)',(pid,lotcode,lot['expiry'],qty,dest,lot['disposition'],lot['waste_category']))
                elif kind in ('PUTAWAY','RECEIVING'):
                    if kind=='RECEIVING' and lot['shelf_id'] is None and dest is None: raise ValueError('Select a destination shelf for receiving.')
                    if kind=='RECEIVING': c.execute('UPDATE lots SET qty=qty+?,shelf_id=coalesce(?,shelf_id) WHERE id=?',(qty,dest,lot['id']))
                    elif dest: c.execute('UPDATE lots SET shelf_id=? WHERE id=?',(dest,lot['id']))
                elif kind=='QUARANTINE_RELEASE':
                    if role not in ('manager','admin'): raise ValueError('Manager role required to release quarantine.')
                    c.execute('UPDATE lots SET quarantined=0,quarantine_reason=NULL WHERE id=?',(lot['id'],))
                elif kind=='ADJUSTMENT':
                    delta=int(data.get('delta',0)); new=int(lot['qty'])+delta
                    if delta==0: raise ValueError('Adjustment quantity must be non-zero.')
                    if new<int(lot['reserved']): raise ValueError('Adjustment cannot reduce stock below its reserved quantity.')
                    if delta>0 and lot['shelf_id']:
                        sh=c.execute('SELECT * FROM shelves WHERE id=?',(lot['shelf_id'],)).fetchone()
                        if not sh or sh['status']!='Available': raise ValueError('Cannot increase stock on an unavailable shelf.')
                        used=c.execute('SELECT coalesce(sum(p.length*p.width*p.height*(l.qty-l.reserved)/1000.0),0) vl,coalesce(sum(p.weight*(l.qty-l.reserved)),0) kg FROM lots l JOIN products p ON p.id=l.product_id WHERE l.shelf_id=?',(sh['id'],)).fetchone()
                        prod=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
                        if prod['length']*prod['width']*prod['height']*delta/1000>sh['capacity_l']-used['vl'] or prod['weight']*delta>sh['max_weight']-used['kg']: raise ValueError('Stock adjustment would exceed the shelf volume or weight capacity.')
                    c.execute('UPDATE lots SET qty=? WHERE id=?',(new,lot['id'])); qty=abs(delta)
                audit(c,actor,kind,pid,{'lot':lotcode,'quantity':qty,'source':source,'destination':dest,'reason':data.get('reason','')})
                c.execute('INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at,status,reference) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(kind,pid,lotcode,qty,source,dest,data.get('reason',''),actor,datetime.utcnow().isoformat(timespec='seconds')+'Z','COMPLETED',str(data.get('reference') or '').strip()))
                c.commit(); c.close(); self.send_json({'ok':True}); return
            if path=='/api/shelf/status':
                if role not in ('manager','admin'): raise ValueError('Manager role required to change shelf availability.')
                sh=c.execute('SELECT * FROM shelves WHERE id=?',(data['shelf_id'],)).fetchone()
                if not sh: raise ValueError('Shelf not found.')
                status=data.get('status')
                if status=='Unavailable' and c.execute("SELECT 1 FROM transfer_orders WHERE dest_shelf=? AND state='IN_TRANSIT' LIMIT 1",(sh['id'],)).fetchone(): raise ValueError('This shelf has an inbound transfer awaiting receipt.')
                if status=='Unavailable' and c.execute('SELECT coalesce(sum(qty),0) FROM lots WHERE shelf_id=? AND qty>0',(sh['id'],)).fetchone()[0]: raise ValueError('Move or resolve all shelf stock before marking it unavailable.')
                if status not in ('Available','Unavailable'): raise ValueError('Invalid shelf status.')
                c.execute('UPDATE shelves SET status=? WHERE id=?',(status,sh['id'])); audit(c,actor,'SHELF_STATUS',sh['id'],{'status':status}); c.commit(); c.close(); self.send_json({'ok':True}); return
            if path=='/api/shelf':
                if role not in ('manager','admin'): raise ValueError('Manager role required to add shelves.')
                x=data; c.execute('INSERT INTO shelves VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(x['id'],x['warehouse_id'],x['zone'],x.get('category_codes','ALL'),x['storage'],float(x['length']),float(x['width']),float(x['height']),float(x['capacity_l']),float(x['max_weight']),int(x['level']),int(bool(x.get('access_easy'))),'Available',int(bool(x.get('restricted'))))); label='NWLOC'+re.sub('[^A-Za-z0-9]','',x['id']).upper(); c.execute('INSERT INTO shelf_labels(shelf_id,barcode) VALUES(?,?)',(x['id'],label)); audit(c,actor,'SHELF_ADDED',x['id'],{'warehouse':x['warehouse_id'],'shelf_barcode':label}); c.commit(); c.close(); self.send_json({'ok':True,'shelf_barcode':label}); return
            if path=='/api/assistant':
                q=str(data.get('question','')).lower(); pid=data.get('product_id'); advice=[]; refs=[]
                product_context=None
                if pid:
                    prod=c.execute('SELECT * FROM products WHERE id=? OR barcode=? OR sku=?',(pid,pid,pid)).fetchone()
                    if prod:
                        product_context={k:prod[k] for k in ('id','name','category_code','category','length','width','height','weight','storage','priority')}
                        refs.append('product:'+prod['id'])
                        lots=c.execute('SELECT l.*,p.storage,p.category_code,p.category,p.length,p.width,p.height,p.weight,p.priority,p.name FROM lots l JOIN products p ON p.id=l.product_id WHERE l.product_id=? AND l.qty>0 ORDER BY CASE WHEN l.expiry IS NULL THEN 1 ELSE 0 END,l.expiry,l.lot_code',(prod['id'],)).fetchall()
                        current=next((lot for lot in lots if not lot['quarantined'] and (not lot['expiry'] or lot['expiry']>date.today().isoformat())),None)
                        recent=[dict(m) for m in c.execute('SELECT kind,qty,source_shelf,dest_shelf,reason,created_at FROM movements WHERE product_id=? ORDER BY id DESC LIMIT 3',(prod['id'],))]
                        product_context['recent_movements']=recent
                        advice.append('Product '+prod['name']+' requires '+prod['storage']+' storage; category '+prod['category']+'; priority '+str(prod['priority'])+'/5.')
                        if current:
                            current=effective_product(current,current)
                            product_context['lot']={'lot_code':current['lot_code'],'quantity':current['qty'],'expiry':current['expiry'],'quarantined':bool(current['quarantined']),'disposition':current['disposition'],'waste_category':current['waste_category']}
                            if current['disposition']=='WASTE': advice.append('Lot condition is waste in the configured '+current['waste_category']+' stream; ordinary product storage does not override that waste-stream rule.')
                            refs.append('lot:'+current['lot_code'])
                            if current['shelf_id']:
                                placed=c.execute('SELECT s.*,w.name warehouse_name FROM shelves s JOIN warehouses w ON w.id=s.warehouse_id WHERE s.id=?',(current['shelf_id'],)).fetchone()
                                used=c.execute('SELECT coalesce(sum(p.length*p.width*p.height*(l.qty-l.reserved)/1000.0),0) vl,coalesce(sum(p.weight*(l.qty-l.reserved)),0) kg FROM lots l JOIN products p ON p.id=l.product_id WHERE l.shelf_id=?',(current['shelf_id'],)).fetchone()
                                advice.append('Lot '+current['lot_code']+' is currently at '+current['shelf_id']+' in '+placed['warehouse_name']+'; shelf status '+placed['status']+', storage '+placed['storage']+', with '+str(round(placed['capacity_l']-used['vl'],1))+' L and '+str(round(placed['max_weight']-used['kg'],1))+' kg remaining.')
                                refs.append('shelf:'+current['shelf_id'])
                            else:
                                choices=allocation_suggestions(c,current,role)
                                if choices:
                                    best=choices[0]
                                    advice.append('For lot '+current['lot_code']+' ('+str(current['qty'])+' units'+(', expires '+current['expiry'] if current['expiry'] else ', no expiry recorded')+'), best eligible location is '+best['shelf_id']+' in '+best['warehouse_name']+'. After placement it would have '+str(best['remaining_l'])+' L and '+str(best['remaining_kg'])+' kg capacity remaining. Rules passed: '+', '.join(best['reasons'])+'.')
                                    if len(choices)>1:
                                        advice.append('Alternatives: '+'; '.join(x['shelf_id']+' ('+str(x['remaining_l'])+' L free after placement)' for x in choices[1:4])+'.')
                                    refs.extend('shelf:'+x['shelf_id'] for x in choices[:3])
                                else:
                                    advice.append('No location currently passes storage, category, dimensions, remaining volume/weight, availability, and configured role rules; review product data or receiving details without overriding those rules.')
                            if current['expiry'] and current['expiry']<= (date.today()+timedelta(days=30)).isoformat():
                                advice.append('Expiry attention: this lot expires on '+current['expiry']+'; follow local FEFO and disposition rules.')
                        elif lots:
                            advice.append('No ordinary allocation is available: remaining stock is quarantined or expired. Review the lot and authorized disposition workflow.')
                        else:
                            advice.append('No on-hand lot exists for this product; lot, quantity, and expiry data are needed before a location recommendation can be made.')
                        if recent:
                            advice.append('Latest movement: '+recent[0]['kind']+' '+str(recent[0]['qty'])+' units on '+recent[0]['created_at']+'; from '+str(recent[0]['source_shelf'] or 'receiving')+' to '+str(recent[0]['dest_shelf'] or 'no shelf')+'.')
                    else: advice.append('I could not find that product ID, SKU, or barcode. Scan again or use manual search.')
                if any(x in q for x in ['expiry','expir','lot']): advice.append('Review lot dates before picking. Use FEFO (first expiry, first out) when product policy permits; quarantined/expired lots are excluded from ordinary recommendations.')
                if any(x in q for x in ['capacity','space','full']): advice.append('Capacity matching checks remaining volume, weight, and item dimensions. Consider another compatible shelf; do not relax storage or safety rules to gain space.')
                if any(x in q for x in ['barcode','scan']): advice.append('A scanner works as keyboard input: focus the scan field, scan product or shelf label, then review the match. Scan alone never confirms movement.')
                if any(x in q for x in ['waste','medical','battery','radioactive','sharps']): advice.append('Restricted waste streams require locally configured authorization and handling. This prototype does not determine legal classification or certify safe storage.')
                if not advice: advice=['I can explain matching, capacity, barcodes, expiry, quarantine, or waste workflows. Ask a specific question or enter a product ID for a grounded suggestion.']
                answer=' '.join(advice); mode='rule-based Smart Slots assistant'
                if OPENAI_API_KEY:
                    facts={'warehouse_count':c.execute('SELECT count(*) FROM warehouses').fetchone()[0],
                           'available_locations':c.execute("SELECT count(*) FROM shelves WHERE status='Available'").fetchone()[0],
                           'quarantined_units':c.execute('SELECT coalesce(sum(qty),0) FROM lots WHERE quarantined=1').fetchone()[0],
                           'expiring_30_days':c.execute("SELECT count(*) FROM lots WHERE expiry IS NOT NULL AND expiry<=date('now','+30 day') AND quarantined=0").fetchone()[0],
                           'recent_movements':[dict(m) for m in c.execute('SELECT kind,product_id,qty,source_shelf,dest_shelf,reason,created_at FROM movements ORDER BY id DESC LIMIT 5')],
                           'product':product_context,'rule_summary':'Hard constraints: storage profile, category, dimensions, available volume and weight, available location, quarantine exclusion, configured waste-stream permissions. AI cannot place, move, dispatch, release quarantine, or override these rules.'}
                    payload={'model':OPENAI_MODEL,'store':False,'instructions':'You are Smart Slots, a warehouse operations explanation assistant. Answer in concise plain language using only the supplied current facts. Never invent an allocation or claim that you executed an action. Storage compatibility, capacity, access permissions, quarantine, and regulated waste rules are hard constraints; do not recommend bypassing them. Explain when staff must use the deterministic allocation screen or consult a qualified local safety/compliance owner. The deterministic engine is authoritative for eligible locations.','input':json.dumps({'question':data.get('question',''),'current_facts':facts,'rule_based_guidance':answer},ensure_ascii=False),'max_output_tokens':350}
                    try:
                        req=urllib.request.Request('https://api.openai.com/v1/responses',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+OPENAI_API_KEY,'Content-Type':'application/json'},method='POST')
                        with urllib.request.urlopen(req,timeout=18) as resp: result=json.loads(resp.read().decode())
                        texts=[]
                        for item in result.get('output',[]):
                            if item.get('type')=='message':
                                texts.extend(part.get('text','') for part in item.get('content',[]) if part.get('type')=='output_text')
                        if texts: answer='\n'.join(texts); mode='OpenAI Responses API ('+OPENAI_MODEL+') · deterministic rules remain authoritative'
                    except Exception:
                        mode='rule-based fallback (AI connection unavailable)'
                c.close(); self.send_json({'answer':answer,'basis':refs or ['Smart Slots local rules and current inventory snapshot'],'mode':mode,'autonomous_action':False}); return
            c.close(); self.fail('Not found.',404)
        except (ValueError,KeyError,TypeError,sqlite3.IntegrityError) as e:
            c.rollback(); c.close(); self.fail(str(e),409 if isinstance(e,sqlite3.IntegrityError) else 400)

def main():
    init_db(seed_demo_data=True)
    if len(sys.argv)>1 and sys.argv[1]=='add-user':
        if len(sys.argv)!=4 or sys.argv[3] not in ('operator','manager','admin'):
            print('Usage: python3 norware_app.py add-user USERNAME operator|manager|admin'); return
        username=sys.argv[2].strip()
        if not username or username==DEMO_USER: print('Choose a non-empty staff username different from the demo account.'); return
        one=getpass.getpass('Temporary password (12+ characters): '); two=getpass.getpass('Confirm password: ')
        if len(one)<12 or one!=two: print('Password must be at least 12 characters and both entries must match.'); return
        c=connect()
        try:
            c.execute('INSERT INTO users(username,password_hash,role,force_change) VALUES(?,?,?,1)',(username,pw_hash(one),sys.argv[3])); audit(c,username,'USER_CREATED','user',{'role':sys.argv[3]}); c.commit(); print('Staff account created. Require password change on first sign-in.')
        except sqlite3.IntegrityError: print('That username already exists.')
        finally: c.close()
        return
    global DEMO_DB_PATH
    demo_data_dir=tempfile.TemporaryDirectory(prefix='norware-demo-')
    DEMO_DB_PATH=Path(demo_data_dir.name)/'demo.sqlite3'
    init_db(DEMO_DB_PATH,seed_demo_data=True,seed_users=False)
    print('Smart Slots local server: http://%s:%s'%(HOST,PORT)); print('Staff demo account: %s (temporary password; change at first login)'%DEMO_USER); print('Local-only mode. Do not expose this demo server to a public network.')
    ThreadingHTTPServer((HOST,PORT),App).serve_forever()
if __name__=='__main__': main()
