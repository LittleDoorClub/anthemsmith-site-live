"""Original-run restoration is bounded; no payment or customer notification."""
import json
import os
from unittest.mock import patch
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recovery_restore_original as r


ORDER = 'AS-FIXTURE-ORIGINAL'


def fixture(phone='+12025550104', order=ORDER):
    fields={'ORDER_ID':order,'IG_URL':'','CATEGORY':'My Story','MOOD':'Fixture mood','VOICE':'female',
            'DETAILS':'','DELIVERY':'sms','SMS_TO':phone,'EMAIL':'','PAID_STATUS':'paid_claimed','REFERENCE_URL':''}
    return ('\n'.join('2020-01-01T00:00:00Z   '+k+': '+v for k,v in fields.items())).encode()


class RestoreTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            'ANTHEMSMITH_LEGACY_ORDER_ID': ORDER, 'ANTHEMSMITH_LEGACY_CONTACT_SUFFIX': '0104',
            'ANTHEMSMITH_LEGACY_RUN_ID': '101', 'ANTHEMSMITH_LEGACY_JOB_ID': '102',
        })
        env.start()
        self.addCleanup(env.stop)

    def test_original_values_preserved_without_fabricating_consent(self):
        brief,delivery=r.parse_original(fixture())
        self.assertEqual(brief['mood'],'Fixture mood')
        self.assertEqual(brief['intake'],'photos')
        self.assertFalse(delivery['consent_verified'])
        self.assertEqual(brief['details'],'')

    def test_wrong_retry_recipient_and_wrong_order_are_rejected(self):
        for raw in (fixture('+12025550105'),fixture(order='AS-OTHER')):
            with self.assertRaises(r.RecoveryError):r.parse_original(raw)

    def test_restore_hash_only_evidence_no_payment_or_token_mutation(self):
        row={'order_id':ORDER,'status':'awaiting_sources','payment_state':'unverified','payment_ref':None,'brief':{},'delivery':{}}
        def db(params,body=None):
            if body:
                self.assertEqual(set(body),{'brief','delivery'})
                self.assertEqual(params['brief'],'eq.{}')
                row.update(body)
            return [row.copy()]
        result=r.restore(fixture(),db=db)
        self.assertFalse(result['consent_verified']);self.assertFalse(result['sent'])
        self.assertNotIn('+12025550104',json.dumps(result))
        self.assertEqual(row['payment_state'],'unverified');self.assertIsNone(row['payment_ref'])
        self.assertEqual(r.restore(fixture(),db=db)['result'],'already_restored')

    def test_different_private_context_is_not_overwritten(self):
        row={'order_id':ORDER,'status':'awaiting_sources','payment_state':'unverified','payment_ref':None,'brief':{'details':'keep me'},'delivery':{}}
        with self.assertRaisesRegex(r.RecoveryError,'conflicts'):r.restore(fixture(),db=lambda *a:[row])

    def test_payment_evidence_is_not_changed_or_guessed(self):
        row={'order_id':ORDER,'status':'awaiting_sources','payment_state':'verified_paid','payment_ref':'real-ref','brief':{},'delivery':{}}
        with self.assertRaisesRegex(r.RecoveryError,'review_required'):r.restore(fixture(),db=lambda *a:[row])

if __name__=='__main__':unittest.main()
