import unittest
import tempfile
import copy,json
from unittest.mock import patch,Mock
from datetime import datetime,timezone
from enrichment import capture, studio_record, bundle,compact_checkpoint,restore_checkpoint
from us_collector import collect_market


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.item={'id':100,'teacher':{'id':7,'name':'Public Instructor'},'duration_minutes':50,
          'retail_price_in_credits':0,'is_livestream':False,'availability':{'status':'available'},
          'new_source_attribute':{'nested':['retained',False]},
          'class':{'id':12,'name':'Strength','activities':'Strength Training, Running',
                   'level':'Open Level','description':'Class description','future_class_field':42},
          'venue':{'id':9,'name':'Studio','alias':'studio','amenities':{'showers':False},
                   'website':'','phone_number':None,'future_studio_field':'retained'}}
        self.state={'studios':{},'classes':{},'sessions':{}}
        self.row={'event_id':'classpass:100'}
        self.when='2026-10-31T07:00:00+00:00'

    def test_unknown_source_fields_preserved_at_each_entity_level(self):
        capture(self.item,self.row,self.state,{}, {},self.when)
        self.assertEqual(self.state['sessions']['classpass:100']['raw_session']['new_source_attribute'],
                         self.item['new_source_attribute'])
        self.assertEqual(self.state['classes']['9:12']['raw_class']['future_class_field'],42)
        self.assertEqual(self.state['studios']['9']['raw_schedule']['future_studio_field'],'retained')

    def test_credits_boolean_and_absence_are_not_fabricated(self):
        capture(self.item,self.row,self.state,{}, {},self.when)
        details=self.state['sessions']['classpass:100']['details']
        self.assertEqual(details['retail_price_in_credits'],0)
        self.assertIs(details['is_livestream'],False)
        self.assertNotIn('price',details)
        self.assertIsNone(self.state['studios']['9']['details']['phone'])
        self.assertIs(self.state['studios']['9']['details']['amenities']['showers'],False)

    def test_studio_and_class_are_deduplicated_across_sessions(self):
        capture(self.item,self.row,self.state,{}, {},self.when)
        second=dict(self.item,id=101)
        capture(second,{'event_id':'classpass:101'},self.state,{}, {},self.when)
        self.assertEqual(len(self.state['studios']),1)
        self.assertEqual(len(self.state['classes']),1)
        self.assertEqual(len(self.state['sessions']),2)
        self.assertNotIn('venue',self.state['sessions']['classpass:100']['raw_session'])
        self.assertNotIn('class',self.state['sessions']['classpass:100']['raw_session'])

    def test_profile_fills_blank_fields_without_turning_display_count_into_exact_count(self):
        record=studio_record(9,directory={'ratingCount':'2500+','rating':4.9},
          schedule={'phone_number':'','amenities':{'showers':False}},
          profile={'phone_number':'+1 555 0100','website':'https://example.com'})
        self.assertEqual(record['details']['phone'],'+1 555 0100')
        self.assertEqual(record['details']['rating_count_display'],'2500+')
        self.assertIsNone(record['details']['rating_count'])

    def test_bundle_limits_sessions_to_the_actual_database_batch(self):
        capture(self.item,self.row,self.state,{}, {},self.when)
        capture(dict(self.item,id=101),{'event_id':'classpass:101'},self.state,{}, {},self.when)
        value=bundle(self.state,[self.row])
        self.assertEqual([s['event_id'] for s in value['sessions']],['classpass:100'])
        self.assertEqual(len(value['classes']),1)
        self.assertEqual(len(value['studios']),1)

    def test_class_identity_required(self):
        self.item['class'].pop('id')
        with self.assertRaises(ValueError):capture(self.item,self.row,self.state,{}, {},self.when)

    def test_compact_checkpoint_roundtrip_keeps_all_source_fields_and_observation_times(self):
        capture(self.item,self.row,self.state,{}, {},self.when)
        row={**self.row,'description':'Class description'}
        original={'enrichment':self.state,'tasks':{'0':{'rows':[row],'pages':1,'excluded':{}}}}
        expected=copy.deepcopy(original)
        compact=json.loads(json.dumps(compact_checkpoint(original)))
        self.assertNotIn('details',compact['enrichment']['sessions']['classpass:100'])
        self.assertNotIn('description',compact['tasks']['0']['rows'][0])
        self.assertEqual(original,expected)
        restore_checkpoint(compact)
        self.assertEqual(compact,expected)

    def test_profiles_checkpoint_in_groups_and_budget_shortfall_is_retryable(self):
        class Client:
            def __init__(self,count):self.count=count;self.venue_cache={}
            def json(self,method,path):
                self.count+=1
                return {'id':int(path.rsplit('/',1)[1]),'name':'Public studio'}
        market={'id':50,'name':'Missoula','timezone':'America/Denver'}
        venues=[{'id':i} for i in range(1,21)]
        now=datetime(2026,11,1,4,tzinfo=timezone.utc)
        for count in [0,146]:
            with self.subTest(count=count),tempfile.TemporaryDirectory() as root, \
                 patch('us_collector.utcnow',return_value=now), \
                 patch('us_collector.PublicClient',return_value=Client(count)), \
                 patch('us_collector.discover',return_value=(venues,{'directory_complete':True,'excluded_non_us_or_unknown':0})), \
                 patch('us_collector.schedule_pages',return_value=[[]]),patch('builtins.print'):
                store=Mock()
                summary,_,snapshot=collect_market(market,root,max_requests=150,store=store)
                if count==0:
                    self.assertEqual(summary['detail_profiles_pending'],0)
                    self.assertEqual(summary['status'],'complete')
                    self.assertLessEqual(store.save.call_count,14)
                else:
                    self.assertEqual(summary['status'],'partial')
                    self.assertEqual(summary['detail_profiles_pending'],20)
                    self.assertTrue(summary['retryable'])
                    self.assertTrue(snapshot['retryable'])
