"""No browser flags or reused private transaction may authorize another order."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import order_workflow as w
OID='AS-PAYMENTTEST'

class BindingTests(unittest.TestCase):
    def test_unique_verified_reference_works(self):
        row={'order_id':OID,'status':'source_received','payment_state':'verified_paid','payment_ref':'venmo:fixture',
             'brief':{'intake':'photos','category':'My Story','voice':'female','mood':'Life journey'},
             'delivery':{'mode':'sms','sms_to':'+12025550123','email':'','consent_verified':False},'delivery_result':{}}
        upload={'order_id':OID,'status':'ready','object_path':OID+'/12345678-1234-4123-8123-123456789abc.png',
                'byte_size':100,'mime_type':'image/png','sha256':'a'*64}
        def read(table, params):
            if 'payment_ref' in params:return [{'order_id':OID}]
            return [row] if table==w.ORDER_TABLE else [upload]
        result=w.canonical_order(OID,read)
        self.assertEqual(result['paid_status'],'paid_verified')
        self.assertFalse(result['consent_verified'])

    def test_reused_reference_is_blocked_before_upload_or_generation(self):
        row={'order_id':OID,'status':'source_received','payment_state':'verified_paid','payment_ref':'venmo:fixture'}
        def read(table, params):
            if 'payment_ref' in params:return [{'order_id':OID},{'order_id':'AS-ANOTHER'}]
            return [row]
        with self.assertRaisesRegex(w.GateError,'reused_or_unverifiable'):w.canonical_order(OID,read)

    def test_nonverified_status_cannot_authorize(self):
        for state in ['paid_claimed','paid_stripe_return','unverified','refunded']:
            row={'order_id':OID,'status':'source_received','payment_state':state,'payment_ref':'fixture'}
            with self.assertRaisesRegex(w.GateError,'payment_not_verified'):w.canonical_order(OID,lambda *a:[row])

if __name__=='__main__':unittest.main()
