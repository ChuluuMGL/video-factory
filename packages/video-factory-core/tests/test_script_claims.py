import copy
import hashlib
import unittest
from video_factory.runtime_store import canonical
from video_factory.script_claims import bind_approved_catalog


class ClaimsBindingTests(unittest.TestCase):
    def setUp(self):
        self.catalog={'status':'approved','version':'synthetic-v1','approval_reference':'synthetic-human-review',
            'items':[{'sku':'SYNTHETIC A','approval_status':'approved','approved_claims':[
                {'id':'sku_identity','text':'Synthetic A','sku':'SYNTHETIC A','kind':'sku_identity',
                 'source_sha256':'a'*64,'source_location':'front name'},
                {'id':'tone','text':'The box photo shows a muted copper shade.','sku':'SYNTHETIC A',
                 'kind':'box_photo_summary','source_sha256':'a'*64,'source_location':'front photo'}]}]}
        self.prepared={'input':{'binding':{'sku_name':'SYNTHETIC A','approved_claims':[{'id':'old'}]}}}
        self.payload={'sku_name':'SYNTHETIC A','approved_claims':[{'id':'old'}]}
    def apply(self, **kwargs):
        return bind_approved_catalog(self.prepared,self.payload,self.catalog,sku='SYNTHETIC A',
            catalog_sha256=kwargs.get('digest',hashlib.sha256(canonical(self.catalog).encode()).hexdigest()))
    def test_model_and_full_validator_get_same_new_claims_without_mutating_history(self):
        old=copy.deepcopy((self.prepared,self.payload,self.catalog))
        left,right=self.apply()
        self.assertEqual(left['input']['binding']['approved_claims'],right['approved_claims'])
        self.assertEqual(right['claims_catalog_version'],'synthetic-v1')
        self.assertEqual((self.prepared,self.payload,self.catalog),old)
        left['input']['binding']['approved_claims'][0]['text']='changed'
        self.assertEqual(right['approved_claims'][0]['text'],'Synthetic A')
    def test_pending_confirmation_is_not_approval(self):
        self.catalog['status']='final_draft_pending_confirmation'
        with self.assertRaisesRegex(ValueError,'CONFIRMATION_REQUIRED'):self.apply()
    def test_digest_prevents_edit_after_confirmation(self):
        with self.assertRaisesRegex(ValueError,'CATALOG_CHANGED'):self.apply(digest='0'*64)
    def test_cross_sku_or_missing_source_denied(self):
        self.payload['sku_name']='SYNTHETIC B'
        with self.assertRaisesRegex(ValueError,'EXACT_SKU_BINDING_REQUIRED'):self.apply()
        self.payload['sku_name']='SYNTHETIC A'
        self.catalog['items'][0]['approved_claims'][0]['source_sha256']=''
        with self.assertRaisesRegex(ValueError,'PROVENANCE_REQUIRED'):self.apply()
    def test_compiled_input_cannot_be_silently_rebound(self):
        self.prepared,self.payload=self.apply()
        with self.assertRaisesRegex(ValueError,'UNCOMPILED_CLAIMS_INPUT_REQUIRED'):self.apply()
    def test_identity_and_summary_cannot_cross_skus_or_lose_attribution(self):
        c=self.catalog['items'][0]['approved_claims'][1]
        c['sku']='SYNTHETIC B'
        with self.assertRaisesRegex(ValueError,'PROVENANCE_REQUIRED'):self.apply()
        c['sku']='SYNTHETIC A';c['text']='Your hair becomes copper.'
        with self.assertRaisesRegex(ValueError,'ATTRIBUTION_REQUIRED'):self.apply()
