import copy,json,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from mindbody_sync import session_row,utc,venue_patch,PublicClient

class MindbodyTests(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,11,10,tzinfo=timezone.utc)
  self.loc={'id':'10004','attributes':{'name':'Studio','slug':'studio','countryCode':'US','city':'Town',
    'latLon':'40,-74','inventoryRefJson':json.dumps({'mb_site_id':22462,'mb_location_id':1,'mb_master_location_id':1381404,'inventory_source':'MB'})}}
  self.ref={'mb_site_id':22462,'mb_location_id':1,'mb_master_location_id':1381404,'mb_class_id':31418,'inventory_source':'MB'}
  self.item={'attributes':{'subType':'class_time','attributes':{'isCancelled':False,'startTime':'2026-11-10T12:00:00Z','endTime':'2026-11-10T13:00:00Z',
   'course':{'name':'Strength','slug':'strength-studio','description':'Source class'},'inventoryRefJson':json.dumps(self.ref)}}}
 def test_stable_site_specific_identity(self):
  eid,p,_=session_row(self.item,self.loc,self.now)
  self.assertEqual(eid,'mindbody:22462:1:31418');self.assertEqual(p['country'],'US')
  self.item['id']='random-response-uuid';self.assertEqual(session_row(self.item,self.loc,self.now)[0],eid)
 def test_cancelled_past_future_rejected(self):
  a=self.item['attributes']['attributes'];a['isCancelled']=True;self.assertIsNone(session_row(self.item,self.loc,self.now))
  a['isCancelled']=False;self.assertIsNone(session_row(self.item,self.loc,self.now+timedelta(days=1)))
  self.assertIsNone(session_row(self.item,self.loc,self.now-timedelta(days=8)))
 def test_identity_mismatch_rejected(self):
  self.ref['mb_site_id']=999;self.item['attributes']['attributes']['inventoryRefJson']=json.dumps(self.ref)
  with self.assertRaises(ValueError):session_row(self.item,self.loc,self.now)
 def test_unknown_cancellation_not_assumed_active(self):
  del self.item['attributes']['attributes']['isCancelled'];self.assertIsNone(session_row(self.item,self.loc,self.now))
 def test_confirmed_cancellation_updates_existing_only(self):
  self.item['attributes']['attributes']['isCancelled']=True
  self.assertIsNone(session_row(self.item,self.loc,self.now))
  self.assertEqual(session_row(self.item,self.loc,self.now,{'mindbody:22462:1:31418'})[1]['registration_status'],'Cancelled')
 def test_intro_price_never_regular_price(self):
  a=self.item['attributes']['attributes'];a['purchaseOption']={'isSingleSession':True,'isIntroOffer':True,'pricing':{'online':'17.50'}}
  self.assertNotIn('price',session_row(self.item,self.loc,self.now)[1]);a['purchaseOption']['isIntroOffer']=False
  self.assertEqual(session_row(self.item,self.loc,self.now)[1]['price'],17.5)
 def test_timezone_required(self):
  with self.assertRaises(ValueError):utc('2026-11-10T12:00:00')
 def test_no_booking_endpoint(self):
  with self.assertRaises(ValueError):PublicClient().post('orders',{})
if __name__=='__main__':unittest.main()
