"""Original-run restoration is bounded; no payment or customer notification."""
import json
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recovery_restore_original as r


def fixture(phone='+12025556352', order=r.ORDER):
    fields={'ORDER_ID':order,'IG_URL':'','CATEGORY':'My Story','MOOD':'Lif journey','VOICE':'female',
            'DETAILS':'','DELIVERY':'sms','SMS_TO':phone,'EMAIL':'','PAID_STATUS':'paid_claimed','REFERENCE_URL':''}
    return ('\n'.join('2026-09-26T15:48:00.0000000Z   '+k+': '+v for k,v in fields.items())).encode()


class RestoreTests(unittest.TestCase):
    def test_original_values_preserved_without_fabricating_consent(self):
        brief,delivery=r.parse_original(fixture())
        self.assertEqual(brief['mood'],'Lif journey')
        self.assertEqual(brief['intake'],'photos')
        self.assertFalse(delivery['consent_verified'])
        self.assertEqual(brief['details'],'')

    def test_wrong_retry_recipient_and_wrong_order_are_rejected(self):
        for raw in (fixture('+12025558088'),fixture(order='AS-OTHER')):
            with self.assertRaises(r.RecoveryError):r.parse_original(raw)

    def test_restore_hash_only_evidence_no_payment_or_token_mutation(self):
        row={'order_id':r.ORDER,'status':'awaiting_sources','payment_state':'unverified','payment_ref':None,'brief':{},'delivery':{}}
        def db(params,body=None):
            if body:
                self.assertEqual(set(body),{'brief','delivery'})
                self.assertEqual(params['brief'],'eq.{}')
                row.update(body)
            return [row.copy()]
        result=r.restore(fixture(),db=db)
        self.assertFalse(result['consent_verified']);self.assertFalse(result['sent'])
        self.assertNotIn('+12025556352',json.dumps(result))
        self.assertEqual(row['payment_state'],'unverified');self.assertIsNone(row['payment_ref'])
        self.assertEqual(r.restore(fixture(),db=db)['result'],'already_restored')

    def test_different_private_context_is_not_overwritten(self):
        row={'order_id':r.ORDER,'status':'awaiting_sources','payment_state':'unverified','payment_ref':None,'brief':{'details':'keep me'},'delivery':{}}
        with self.assertRaisesRegex(r.RecoveryError,'conflicts'):r.restore(fixture(),db=lambda *a:[row])

    def test_payment_evidence_is_not_changed_or_guessed(self):
        row={'order_id':r.ORDER,'status':'awaiting_sources','payment_state':'verified_paid','payment_ref':'real-ref','brief':{},'delivery':{}}
        with self.assertRaisesRegex(r.RecoveryError,'review_required'):r.restore(fixture(),db=lambda *a:[row])

if __name__=='__main__':unittest.main()
