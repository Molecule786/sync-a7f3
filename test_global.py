import unittest
from datetime import datetime,timedelta,timezone
from global_collector import parse_global,country_of

class GlobalTests(unittest.TestCase):
    def test_country_conflict_is_not_silently_assigned(self):
        self.assertIsNone(country_of({'location':{'country_short_code':'US','msa_id':35}},{35:'GBR'}))

    def test_non_us_class_does_not_require_state(self):
        now=datetime.now(timezone.utc)
        item={'id':123,'verticals':['activity'],'starttime':(now+timedelta(hours=1)).timestamp(),
              'endtime':(now+timedelta(hours=2)).timestamp(),
              'venue':{'id':456,'alias':'london-studio','name':'London studio',
                       'address':{'city':'London','state':None},'location':{'msa_id':35}},
              'class':{'entity_type':'CLASS','name':'Strength','alias':'strength'}}
        row,reason=parse_global(item,{'country_code':'GBR'},{35:'GBR'},now,now+timedelta(days=7),now)
        self.assertIsNone(reason)
        self.assertEqual(row['country'],'GB')
        self.assertNotIn('region',row)
        item['venue']['location']['country_short_code']='US'
        self.assertIsNone(parse_global(item,{'country_code':'GBR'},{35:'GBR'},now,now+timedelta(days=7),now)[0])

if __name__=='__main__':unittest.main()
