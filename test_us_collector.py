import copy,json,unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from us_collector import CollectionError,parse_session,first_directory,schedule_pages,slug,merge_venue_metadata

class USCollectionTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,1,4,tzinfo=timezone.utc)
        self.row={'id':123,'starttime':1793511000,'endtime':1793514600,
          'verticals':['activity'],'class':{'name':'Strength','alias':'strength','entity_type':'CLASS'},
          'venue':{'id':1,'alias':'example','name':'Example','address':{'city':'New York','state':'NY'},'location':{'msa_id':1}}}
    def parse(self,row):
        with patch('us_collector.utcnow',return_value=self.now):
            return parse_session(row,self.now,self.now+timedelta(days=7))
    def test_dst_epoch_times_not_reinterpreted_as_local(self):
        row,reason=self.parse(self.row)
        self.assertIsNone(reason)
        self.assertEqual(datetime.fromisoformat(row['ends_at'])-datetime.fromisoformat(row['starts_at']),timedelta(hours=1))
        self.assertTrue(row['starts_at'].endswith('+00:00'))
    def test_country_search_can_include_canada(self):
        self.row['venue']['address']['state']='ON'
        self.row['venue']['location']['msa_id']=31
        self.assertEqual(self.parse(self.row)[1],'unknown_geography')
    def test_explicit_non_us_country_overrules_ambiguous_state(self):
        self.row['venue']['location']['country_short_code']='CA'
        self.assertEqual(self.parse(self.row)[1],'non_us')
    def test_us_state_alone_without_market_evidence_is_insufficient(self):
        self.row['venue']['location']={}
        self.assertEqual(self.parse(self.row)[1],'unknown_geography')
    def test_beauty_appointments_are_excluded(self):
        self.row['verticals']=['1161']
        self.assertEqual(self.parse(self.row)[1],'not_fitness_class')
    def test_rolling_window_is_half_open(self):
        self.row['starttime']=int((self.now+timedelta(days=7)).timestamp())
        self.row['endtime']=self.row['starttime']+3600
        self.assertEqual(self.parse(self.row)[1],'outside_window')
    def test_blocked_directory_is_not_zero(self):
        with self.assertRaises(CollectionError):first_directory('<h1>Access denied</h1>')
    def test_cursor_pagination_preserves_venue_scope(self):
        class Client:
            def __init__(self):self.calls=[]
            def json(self,method,path,**kwargs):
                self.calls.append((method,kwargs))
                return {'schedules':[{'venue':{'id':1}}],**({'cursor':'published-cursor'} if len(self.calls)==1 else {})}
        c=Client()
        self.assertEqual(len(list(schedule_pages(c,[1],'2026-11-01'))),2)
        self.assertEqual(c.calls[1],('GET',{'params':{'cursor':'published-cursor'}}))
    def test_repeated_cursor_fails_instead_of_looping(self):
        class Client:
            def json(self,*args,**kwargs):return {'schedules':[],'cursor':'same'}
        with self.assertRaises(CollectionError):list(schedule_pages(Client(),[1],'2026-11-01'))
    def test_unrequested_venue_fails(self):
        class Client:
            def json(self,*args,**kwargs):return {'schedules':[{'venue':{'id':2}}]}
        with self.assertRaises(CollectionError):list(schedule_pages(Client(),[1],'2026-11-01'))
    def test_missing_city_uses_verified_published_geography(self):
        venue={'id':38091,'address':{'state':'IN','city':''}}
        detail={'id':38091,'address':{},'global_location':{'msa':{'country':{'short_code':'US'}},'areas':{'city':'Greenwood, IN, USA'}}}
        self.assertEqual(merge_venue_metadata(venue,detail)['address']['city'],'Greenwood')
        detail['global_location']['areas']['city']='Made Up, NY, USA'
        self.assertFalse(merge_venue_metadata(venue,detail)['address'].get('city'))
    def test_missing_class_url_is_reported_as_gap(self):
        self.row['class']['alias']=None
        self.assertEqual(self.parse(self.row)[1],'missing_required_fields')
