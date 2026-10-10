#!/usr/bin/env python3
"""End-to-end smoke check against an isolated temporary NORWARE server/database."""
import http.cookiejar, json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path
from datetime import date, timedelta

HERE=Path(__file__).resolve().parent
def check(ok,msg):
    if not ok: raise AssertionError(msg)
def main():
    with tempfile.TemporaryDirectory(prefix='norware-check-') as td:
        sock=socket.socket(); sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]; sock.close()
        env=os.environ.copy(); env['NORWARE_DB_PATH']=str(Path(td)/'app.sqlite3'); env['NORWARE_PORT']=str(port); env['NORWARE_HOST']='127.0.0.1'; env.pop('OPENAI_API_KEY',None)
        server=subprocess.Popen([sys.executable,str(HERE/'norware_app.py')],cwd=str(HERE),env=env,stdout=subprocess.DEVNULL)
        jar=http.cookiejar.CookieJar(); client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)); base='http://127.0.0.1:'+str(port)
        def request(path,payload=None,ok=True):
            data=json.dumps(payload).encode() if payload is not None else None
            req=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'} if data else {})
            try:
                with client.open(req,timeout=4) as response: result=json.loads(response.read().decode()); status=response.status
            except urllib.error.HTTPError as e: result=json.loads(e.read().decode()); status=e.code
            if ok and status>=400: raise AssertionError(path+' returned '+str(status)+': '+str(result))
            if not ok and status<400: raise AssertionError(path+' should have failed but returned '+str(result))
            return result
        try:
            for _ in range(80):
                try: request('/api/session'); break
                except Exception: time.sleep(.15)
            html=client.open(base+'/',timeout=4).read().decode()
            check('WAREHOUSE INVENTORY OPERATING SYSTEM' in html and 'blueprint' in html,'startup screen assets missing')
            check('countdown' in html and 'n<=0' in html,'boot timeout implementation missing')
            d=request('/api/entry/demo',{}); check(d['session']['demo_id'].startswith('DEMO-'),'temporary demo ID missing')
            check('expires_at' in d['session'],'demo session expiry missing')
            b=request('/api/bootstrap'); check(len(b['warehouses'])==7,'expected 7 warehouses')
            check(len(b['shelves'])>=133,'expected sample locations, Waste sections, and freezer shelf')
            check(b['product_count']>=10005,'expected imported catalog plus demo products')
            check(len(b['waste_categories'])==13,'waste taxonomy missing')
            check(any(x['lot_code']=='LOT-OAT-2601' and x['expiry'] for x in b['lots']),'demo lot expiry data missing')
            check({'Food waste','Batteries','Medical: infectious','Medical: radioactive'}.issubset({x['zone'] for x in b['shelves']}),'separate waste stream locations missing')
            plastic=request('/api/search?q=8908006727421'); check(any(x['id']=='DEMO-PLAST-01' and x['name'].lower().startswith('plastic bottle') for x in plastic['products']),'Plastic Bottle barcode lookup failed')
            plastic_lot=next(x for x in b['lots'] if x['product_id']=='DEMO-PLAST-01')
            request('/api/lot/disposition',{'lot_id':plastic_lot['id'],'disposition':'WASTE','waste_category':'Plastics'})
            demo_changed=next(x for x in request('/api/bootstrap')['lots'] if x['id']==plastic_lot['id'])
            check(demo_changed['disposition']=='WASTE' and demo_changed['waste_category']=='Plastics','isolated demo condition change was not saved')
            request('/api/products',{'id':'DEMO-WRITE','sku':'DEMO-WRITE','barcode':'2222222222222','name':'demo-only product','category':'FOOD','length':1,'width':1,'height':1,'weight':1,'storage':'AMBIENT','priority':1,'quantity':1,'lot':'X'})
            found=request('/api/search?q=8901000000011'); check(found['products'][0]['id']=='DEMO-FOOD-01','product barcode lookup failed')
            manual=request('/api/search?q=DEMO-FOOD-01'); check(manual['products'][0]['id']=='DEMO-FOOD-01','manual product ID lookup failed')
            demo_suggest=request('/api/suggest',{'product_id':'DEMO-FOOD-01','lot':'LOT-OAT-2601'}); check(demo_suggest['eligible_count']>0,'compatible chilled suggestions missing')
            check(demo_suggest['suggestions'][0]['remaining_l']>=0 and demo_suggest['suggestions'][0]['reasons'],'suggestion explanation/capacity missing')
            request('/api/allocate',{'product_id':'DEMO-FOOD-01','lot':'LOT-OAT-2601','shelf_id':demo_suggest['suggestions'][0]['shelf_id']},ok=False)
            request('/api/logout',{})
            request('/api/login',{'username':'staff.demo','password':'incorrect'},ok=False)
            login=request('/api/login',{'username':'staff.demo','password':env.get('NORWARE_DEMO_PASSWORD','123')}); check(login['session']['force_change'],'first-login password change not required')
            request('/api/allocate',{'product_id':'DEMO-FOOD-01','lot':'LOT-OAT-2601','shelf_id':demo_suggest['suggestions'][0]['shelf_id']},ok=False)
            request('/api/password',{'password':'New!WarehousePass-2026'})
            check(not request('/api/search?q=DEMO-WRITE')['products'],'local demo product leaked into persistent staff records')
            staff_boot=request('/api/bootstrap'); plastic_lot=next(x for x in staff_boot['lots'] if x['product_id']=='DEMO-PLAST-01')
            check(plastic_lot['disposition']=='USEFUL','local demo condition change leaked into persistent staff records')
            request('/api/lot/disposition',{'lot_id':plastic_lot['id'],'disposition':'WASTE','waste_category':'Plastics'})
            changed=request('/api/bootstrap'); changed_lot=next(x for x in changed['lots'] if x['id']==plastic_lot['id'])
            check(changed_lot['disposition']=='WASTE' and changed_lot['waste_category']=='Plastics','lot waste disposition was not saved')
            waste_places=request('/api/suggest',{'product_id':'DEMO-PLAST-01','lot_id':plastic_lot['id']})
            check(waste_places['eligible_count']>0 and all(x['warehouse_id']=='WH007' for x in waste_places['suggestions']),'waste lot received a non-Waste allocation suggestion')
            request('/api/lot/disposition',{'lot_id':plastic_lot['id'],'disposition':'USEFUL'})
            reusable=request('/api/suggest',{'product_id':'DEMO-PLAST-01','lot_id':plastic_lot['id']})
            check(reusable['eligible_count']>0 and all(x['warehouse_id']!='WH007' for x in reusable['suggestions']),'useful lot retained a Waste warehouse suggestion')
            check(any(a['action']=='LOT_DISPOSITION_CHANGED' for a in request('/api/bootstrap')['audit']),'lot disposition change was not audited')
            request('/api/products',{'id':'TEST-FOOD-01','sku':'TEST-FOOD-01','barcode':'5555555555579','name':'Test chilled food carton','category':'FOOD','category_name':'Food & beverages','length':32,'width':24,'height':20,'weight':8,'storage':'CHILLED','priority':5,'quantity':12,'lot':'TEST-FOOD-LOT','expiry':(date.today()+timedelta(days=20)).isoformat()})
            request('/api/products',{'id':'TEST-BATT-01','sku':'TEST-BATT-01','barcode':'5555555555586','name':'Test quarantined battery','category':'WASTE','category_name':'Batteries','disposition':'WASTE','waste_category':'Batteries','length':18,'width':12,'height':8,'weight':1.8,'storage':'DRY_SECURE','priority':1,'quantity':1,'lot':'TEST-BATT-LOT','quarantined':True,'quarantine_reason':'test quarantine'})
            assistant=request('/api/assistant',{'question':'Where should this high-priority lot go and when does it expire?','product_id':'TEST-FOOD-01'})
            check('best eligible location' in assistant['answer'] and 'expires' in assistant['answer'] and assistant['autonomous_action'] is False,'assistant did not ground allocation guidance in current lot and capacity data')
            request('/api/products',{'id':'TEST-MISMATCH','sku':'TEST-MISMATCH','barcode':'5555555555555','name':'Incompatible freezer chemical','category':'CHEM','category_name':'Chemicals','length':10,'width':10,'height':10,'weight':1,'storage':'FROZEN','priority':3,'quantity':1,'lot':'MISMATCH-LOT'})
            mismatch=request('/api/suggest',{'product_id':'TEST-MISMATCH','lot':'MISMATCH-LOT'}); check(mismatch['eligible_count']==0,'category/profile mismatch was suggested')
            request('/api/products',{'id':'TEST-CAP','sku':'TEST-CAP','barcode':'5555555555562','name':'Over-capacity item','category':'FOOD','category_name':'Food','length':299,'width':299,'height':299,'weight':1,'storage':'CHILLED','priority':3,'quantity':100,'lot':'CAP-LOT'})
            cap=request('/api/suggest',{'product_id':'TEST-CAP','lot':'CAP-LOT'}); check(cap['eligible_count']==0,'capacity limit was not enforced')
            cold_shelf=next(x['id'] for x in b['shelves'] if x['warehouse_id']=='WH002' and x['storage']=='CHILLED')
            request('/api/allocate',{'product_id':'TEST-CAP','lot':'CAP-LOT','shelf_id':cold_shelf},ok=False)
            for row in b['shelves']:
                if row['warehouse_id']=='WH002' and row['status']=='Available' and not row['stock_units']:
                    shelf=row['id']; break
            request('/api/shelf/status',{'shelf_id':shelf,'status':'Unavailable'}); off=request('/api/bootstrap'); check(next(x for x in off['shelves'] if x['id']==shelf)['status']=='Unavailable','shelf did not become unavailable')
            check(shelf not in [x['shelf_id'] for x in request('/api/suggest',{'product_id':'TEST-FOOD-01','lot':'TEST-FOOD-LOT'})['suggestions']],'unavailable shelf remained eligible')
            request('/api/shelf/status',{'shelf_id':shelf,'status':'Available'})
            barcode=request('/api/search?q=NWLOC'+shelf.replace('-','')); check(barcode['shelves'][0]['id']==shelf,'shelf barcode lookup failed')
            allocated=request('/api/suggest',{'product_id':'TEST-FOOD-01','lot':'TEST-FOOD-LOT'}); first=allocated['suggestions'][0]['shelf_id']
            request('/api/allocate',{'product_id':'TEST-FOOD-01','lot':'TEST-FOOD-LOT','shelf_id':first})
            after=request('/api/bootstrap'); check(any(m['kind']=='PUTAWAY' and m['product_id']=='TEST-FOOD-01' for m in after['movements']),'confirmed allocation movement missing')
            food_lot=next(x for x in after['lots'] if x['product_id']=='TEST-FOOD-01' and x['lot_code']=='TEST-FOOD-LOT')
            nexts=request('/api/suggest',{'product_id':'TEST-FOOD-01','lot':'TEST-FOOD-LOT'})['suggestions']; dest=next(x['shelf_id'] for x in nexts if x['shelf_id']!=first)
            transfer=request('/api/transfer/start',{'product_id':'TEST-FOOD-01','lot_id':food_lot['id'],'qty':2,'dest_shelf':dest,'reason':'integration check'})
            request('/api/transfer/receive',{'transfer_id':transfer['transfer_id'],'shelf_id':dest,'reason':'integration check'})
            moved_lot=next(x for x in request('/api/bootstrap')['lots'] if x['product_id']=='TEST-FOOD-01' and x['shelf_id']==dest)
            request('/api/movement',{'kind':'ADJUSTMENT','product_id':'TEST-FOOD-01','lot_id':moved_lot['id'],'lot':'TEST-FOOD-LOT','qty':100000,'delta':100000,'source_shelf':dest,'reason':'capacity rejection check'},ok=False)
            request('/api/movement',{'kind':'DISPATCH','product_id':'TEST-FOOD-01','lot_id':moved_lot['id'],'lot':'TEST-FOOD-LOT','qty':1,'source_shelf':dest,'reason':'integration check'})
            final=request('/api/bootstrap'); check(sum(x['qty'] for x in final['lots'] if x['product_id']=='TEST-FOOD-01')==11,'transfer/dispatch stock totals incorrect')
            request('/api/suggest',{'product_id':'TEST-BATT-01','lot':'TEST-BATT-LOT'},ok=False)
            request('/api/movement',{'kind':'DISPATCH','product_id':'TEST-BATT-01','lot':'TEST-BATT-LOT','qty':1},ok=False)
            request('/api/movement',{'kind':'QUARANTINE_RELEASE','product_id':'TEST-BATT-01','lot':'TEST-BATT-LOT','qty':1,'reason':'manager review'})
            assistant=request('/api/assistant',{'question':'Explain capacity and barcode scans','product_id':'TEST-FOOD-01'}); check(assistant['autonomous_action'] is False and 'fallback' not in assistant['mode'].lower() and assistant['mode'].startswith('rule-based'),'assistant no-key fallback missing')
            check(any(a['action']=='ALLOCATION_CONFIRMED' for a in final['audit']),'audit trail missing confirmed allocation')
            print('PASS: BIOS page/timeout, isolated demo session, 7 warehouses/locations, staff login/forced password change, product barcode and condition workflows, product and shelf barcode lookup, storage/category/capacity rejection, shelf availability, staff-confirmed allocation, transfer, dispatch, quarantine exclusion/release, and rule-based AI fallback.')
        finally:
            server.terminate()
            try: server.wait(timeout=3)
            except subprocess.TimeoutExpired: server.kill()
if __name__=='__main__': main()
