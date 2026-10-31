import unittest
from enrichment import capture, studio_record, bundle


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
