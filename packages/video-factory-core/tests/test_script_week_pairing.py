import copy
import hashlib
import unittest
from video_factory.runtime_store import fingerprint, RuntimeFault
from video_factory.script_week_pairing import freeze_inputs, pair_baseline
from video_factory.script_week_archive import OUTPUTS


class PairingTests(unittest.TestCase):
    def setUp(self):
        inputs={'task':{'record_id':'recFixture','fields':{'brief':'approved input'}},'materials':{}}
        self.linked={'inputs':inputs,'input_sha256':fingerprint(inputs),
                     'provenance':{'base':'basFixture','task_table':'tblFixture','task_record':'recFixture'}}
        self.doc={'document_id':'docFixture','revision':7,'content':'approved plan'}
        self.args=dict(approved_document='docFixture',approved_revision=7,
                       approved_content_sha256=hashlib.sha256(b'approved plan').hexdigest(),
                       compiler_sha256='a'*64,claims_sha256='b'*64)
        self.frozen=freeze_inputs(self.linked,self.doc,**self.args)
        self.outputs=dict.fromkeys(OUTPUTS,'Original fixture output')
        self.receipt={**self.linked['provenance'],'producer':'n8n','unmodified':True,
                      'shared_input_sha256':self.frozen['shared_input_sha256'],
                      'outputs':self.outputs,'outputs_sha256':fingerprint(self.outputs),
                      'execution_receipt_sha256':'c'*64}

    def test_matching_original_receipt_and_visible_outputs(self):
        result=pair_baseline(self.frozen,self.receipt,self.outputs)
        self.assertEqual(result['status'],'matched')
        self.outputs['video_script']='later edit'
        self.assertNotEqual(result['outputs'],self.outputs)

    def test_missing_manual_or_changed_input_never_matches(self):
        self.assertEqual(pair_baseline(self.frozen,None,self.outputs)['reason'],'production_execution_receipt_missing')
        for field,value,reason in [('producer','manual_handoff','not_unmodified_n8n_output'),
                                   ('unmodified',False,'not_unmodified_n8n_output'),
                                   ('shared_input_sha256','d'*64,'input_version_mismatch'),
                                   ('task_record','recOther','source_identity_mismatch')]:
            self.assertEqual(pair_baseline(self.frozen,{**self.receipt,field:value},self.outputs)['reason'],reason)

    def test_visible_text_equality_alone_is_not_provenance(self):
        for field in ('outputs_sha256','execution_receipt_sha256'):
            receipt=copy.deepcopy(self.receipt);receipt.pop(field)
            self.assertEqual(pair_baseline(self.frozen,receipt,self.outputs)['status'],'unpaired_or_pending')
        changed={**self.outputs,'video_script':'Human edit'}
        self.assertEqual(pair_baseline(self.frozen,self.receipt,changed)['reason'],'visible_outputs_changed')

    def test_plan_and_linked_changes_are_rejected(self):
        for key,value in [('document_id','docOther'),('revision',8),('content','changed')]:
            with self.assertRaises(RuntimeFault):freeze_inputs(self.linked,{**self.doc,key:value},**self.args)
        bad=copy.deepcopy(self.linked);bad['inputs']['task']['fields']['brief']='changed'
        with self.assertRaises(RuntimeFault):freeze_inputs(bad,self.doc,**self.args)

    def test_candidate_policy_separate_from_shared_input(self):
        other=freeze_inputs(self.linked,self.doc,**{**self.args,'claims_sha256':'e'*64})
        self.assertEqual(self.frozen['shared_input_sha256'],other['shared_input_sha256'])
        self.assertNotEqual(self.frozen['candidate_policy'],other['candidate_policy'])
        self.linked['inputs']['task']['fields']['brief']='edit after freeze'
        self.assertEqual(self.frozen['shared_input']['inputs']['task']['fields']['brief'],'approved input')

if __name__=='__main__':unittest.main()
