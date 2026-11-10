import unittest
from retention import confirmed

class RetentionTests(unittest.TestCase):
 def setUp(self):
  self.before={'event_id':'classpass:123','country':'US','starts_at':'2026-11-01T10:00:00Z','ends_at':'2026-11-01T11:00:00Z'}
  self.cutoff='2026-11-09T00:00:00Z'
 def test_exact_source_ended_event_can_be_eligible(self):
  self.assertTrue(confirmed(self.before,dict(self.before),self.cutoff))
 def test_rescheduled_and_recently_ended_source_are_retained(self):
  for ends in ['2026-11-12T11:00:00Z','2026-11-09T02:00:00Z']:
   self.assertFalse(confirmed(self.before,{**self.before,'ends_at':ends},self.cutoff))
 def test_wrong_identity_and_country_are_retained(self):
  for patch in [{'event_id':'classpass:124'},{'country':'CA'}]:
   self.assertFalse(confirmed(self.before,{**self.before,**patch},self.cutoff))
 def test_invalid_or_ambiguous_dates_are_retained(self):
  for value in ['invalid','2026-11-01T09:00:00Z','2026-11-01T11:00:00']:
   self.assertFalse(confirmed(self.before,{**self.before,'ends_at':value},self.cutoff))
