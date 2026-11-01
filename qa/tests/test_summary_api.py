"""Dispatcher + real temporary SQLite; model transport mocked, no private key."""
import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from app import Application
class SummaryAPI(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.app=Application(Path(self.tmp.name)/'db.sqlite3');self.app.services.key='dummy-test-key'
 def tearDown(self):self.tmp.cleanup()
 def test_ranges(self):
  for period,days in [('day',1),('week',7),('month',30)]:
   code,data=self.app.dispatch('GET','/api/financial-summary?period='+period)
   self.assertEqual((code,data['current']['days'],data['prior']['days']),(200,days,days));self.assertFalse(data['coverage']['complete'])
 def test_date_not_client_controlled(self):
  data=self.app.dispatch('GET','/api/financial-summary?period=day&as_of=1900-01-01')[1]
  self.assertNotEqual(data['as_of'],'1900-01-01')
 def test_consent(self):
  with self.assertRaises(ValueError):self.app.dispatch('POST','/api/ai/summary',{'period':'week'})
 def test_bad_period(self):
  with self.assertRaises(ValueError):self.app.dispatch('GET','/api/financial-summary?period=year')
 def test_summary_wire_and_read_only(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self,*a):return json.dumps({'choices':[{'message':{'content':'測試摘要，非真AI'}}]}).encode()
  before=self.app.dispatch('GET','/api/backup')[1]
  with patch('services.safe_open',return_value=Response()) as network:
   code,data=self.app.dispatch('POST','/api/ai/summary',{'period':'month','consent':True})
   wire=json.loads(network.call_args.args[0].data)
  self.assertEqual(code,200);self.assertEqual(data['statistics']['period'],'month')
  self.assertIn('財務摘要',wire['messages'][0]['content']);self.assertNotIn('dummy-test-key',json.dumps(wire))
  self.assertEqual(before,self.app.dispatch('GET','/api/backup')[1])
if __name__=='__main__':unittest.main()
