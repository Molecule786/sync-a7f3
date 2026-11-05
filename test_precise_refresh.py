import json,unittest
from pathlib import Path
from precise_refresh import proposal,session_row
from collector import CollectionError

class PreservationTests(unittest.TestCase):
    def test_unchanged_and_missing_values_do_not_erase_details(self):
        old={'event_id':'classpass:1','title':'Yoga','description':'Detailed existing description',
             'price':25,'country':'US','city':'New York','city_slug':'custom-city',
             'lat':40.7,'url':'https://classpass.com/classes/a/b/1'}
        fresh={'event_id':'classpass:1','title':' yoga ','description':None,'country':'US',
               'city':'New York','city_slug':'new-york','lat':40.70001,'price':5,
               'url':'https://classpass.com/classes/new/b/1'}
        self.assertEqual(proposal(fresh,old,'now')['patch'],{})
        self.assertEqual(old['price'],25)

    def test_identity_and_country_conflicts_are_retained(self):
        with self.assertRaises(CollectionError):proposal({'event_id':'classpass:1'},None,'now',2)
        with self.assertRaises(CollectionError):proposal({'event_id':'classpass:1','country':'GB'},{'country':'US'},'now')

if __name__=='__main__':unittest.main()
