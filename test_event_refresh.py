import json,unittest
from event_refresh import parse_active,parse_opensports,proposal,SourceError,timestamp

class RefreshTests(unittest.TestCase):
 def active(self):
  b={'event_id':'active:abc','url':'https://www.active.com/city/classes/yoga-2026'}
  d={'assetId':'abc','eventDetails':{'eventTitle':'Yoga'},'addToCalender':{
   'googleCalendar':'https://calendar.google.com/calendar/render?dates=20261114T150000Z%2F20261114T160000Z'}}
  return b,d
 def test_active_calendar_is_utc(self):
  b,d=self.active();p='<script data-component-name="EventLanding">'+json.dumps(d)+'</script>'
  r=parse_active(p,b);self.assertEqual(r['starts_at'],'2026-11-14T15:00:00+00:00')
 def test_active_wrong_identity_rejected(self):
  b,d=self.active();d['assetId']='other'
  with self.assertRaises(SourceError):parse_active('<script data-component-name="EventLanding">'+json.dumps(d)+'</script>',b)
 def test_naive_legacy_dates_are_not_assumed_utc(self):
  b,_=self.active();p='window["asset"]='+json.dumps({'assetGuid':'abc','assetName':'Yoga','activityStartDate':'2026-11-14T09:00'})
  with self.assertRaises(SourceError):parse_active(p,b)
 def test_schema_offset_preserved(self):
  b,_=self.active();p='window["asset"]='+json.dumps({'assetGuid':'abc','assetName':'Yoga'})+';'
  p+='<script type="application/ld+json">'+json.dumps({'@type':'Event','url':b['url'],'startDate':'2026-11-14T09:00:00-0800','endDate':'2026-11-14T10:00:00-0800'})+'</script>'
  self.assertEqual(parse_active(p,b)['starts_at'],'2026-11-14T17:00:00+00:00')
 def test_opensports_uses_source_utc_not_local_display(self):
  b={'event_id':'opensports:7','url':'https://opensports.net/posts/yoga-seven'}
  post={'id':7,'aliasID':'yoga-seven','isPublic':True,'title':'Yoga','start':'2026-11-05T20:00:00Z','end':'2026-11-05T21:00:00Z','timeZone':'Australia/Sydney'}
  p='<script id="__NEXT_DATA__">'+json.dumps({'props':{'initialState':{'postDetail':{'list':[post]}}}})+'</script>'
  self.assertEqual(parse_opensports(p,b)['ends_at'],'2026-11-05T21:00:00+00:00')
 def test_empty_values_and_unrelated_fields_preserved(self):
  before={'title':'Yoga','description':'Keep','price':25,'lat':10,'starts_at':'2026-11-14T15:00:00Z'}
  self.assertEqual(proposal({'title':'Yoga','description':'','price':0,'lat':20,'starts_at':'2026-11-14T15:00:00+00:00'},before),{})
 def test_missing_timezone_rejected(self):
  with self.assertRaises(SourceError):timestamp('2026-11-14T09:00:00')
 def test_filled_location_waits_for_venue_refresh_phase(self):
  self.assertEqual(proposal({'city':'New city','venue':'New studio','description':'New details'},
   {'city':'Old city','venue':'Old studio','description':'Old details'}),{'description':'New details'})

if __name__=='__main__':unittest.main()
