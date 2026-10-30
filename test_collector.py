import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from collector import CollectionError, parse_schedule, timestamp


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(Path('config.json').read_text())
        self.now = datetime(2026, 10, 30, 0, tzinfo=timezone.utc)
        self.event = {'@type':'Event','name':'Strength class',
            'url':'https://classpass.com/classes/studio/strength/12345',
            'startDate':'2026-10-30T12:00:00Z','endDate':'2026-10-30T12:50:00Z',
            'location':{'name':'Example studio','address':'Example street'}}

    def parse(self, event):
        html = '<section data-testid="Schedule"></section><script type="application/ld+json">' + json.dumps({'@graph':[event]}) + '</script>'
        return parse_schedule(html,self.config,self.now)

    def test_stable_id_does_not_depend_on_title_or_time(self):
        changed = copy.deepcopy(self.event)
        changed['name']='New title'
        changed['startDate']='2026-10-30T12:10:00Z'
        self.assertEqual(self.parse(self.event)[0]['event_id'],self.parse(changed)[0]['event_id'])

    def test_utc_dates_and_no_invented_price(self):
        row=self.parse(self.event)[0]
        self.assertEqual(timestamp(row['starts_at']).hour,12)
        self.assertNotIn('price',row)
        self.assertNotIn('registration_status',row)

    def test_expired_or_outside_window_excluded(self):
        for start,end in [('2026-10-29T12:00:00Z','2026-10-29T13:00:00Z'),('2026-11-10T12:00:00Z','2026-11-10T13:00:00Z')]:
            self.event.update(startDate=start,endDate=end)
            self.assertEqual(self.parse(self.event),[])

    def test_missing_timezone_rejected(self):
        self.event['startDate']='2026-10-30T12:00:00'
        with self.assertRaises(CollectionError):self.parse(self.event)

    def test_bad_id_rejected(self):
        self.event['url']='https://example.com/classes/studio/strength/12345'
        with self.assertRaises(CollectionError):self.parse(self.event)

    def test_blocked_page_is_not_an_empty_success(self):
        with self.assertRaises(CollectionError):parse_schedule('<h1>Access denied</h1>',self.config,self.now)


if __name__=='__main__': unittest.main()
