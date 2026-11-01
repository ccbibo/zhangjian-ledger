"""Reject non-object payloads before they can clear provider configurations."""
import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from app import Application
class Payloads(unittest.TestCase):
 def test_nonobjects_do_not_clear_keys(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{},clear=True):
   app=Application(Path(tmp)/'db.sqlite3');app.services.key='P0_DUMMY_AI';app.market_key='P0_DUMMY_MARKET'
   for path in ['/api/ai/configure','/api/markets/configure','/api/transaction/create','/api/settings']:
    for payload in [[],False,0,'',True,1,'invalid']:
     with self.subTest(path=path,payload=payload):
      with self.assertRaisesRegex(ValueError,'物件'):app.dispatch('POST',path,payload)
      self.assertEqual(app.services.key,'P0_DUMMY_AI');self.assertEqual(app.market_key,'P0_DUMMY_MARKET')
if __name__=='__main__':unittest.main()
