import copy
import unittest
from video_factory.script_content_policy import normalize_and_guard, strengthen_messages, RULES
from video_factory.script_receipts import ValidationFailure


class ContentPolicyTests(unittest.TestCase):
    def draft(self):
        return {'decision':'draft','goal_answer':{'claim_ids':['identity']},'storyboard':{'shots':[
            {'visual_evidence':'generated_visualization','asset_references':['tone_reference'],
             'visual_description':'同一服装、同一发型。无包装，无持盒动作。',
             'voiceover_en':'I like this shade.','on_screen_text_en':'I like this shade.'},
            {'visual_evidence':'generated_context','asset_references':['approved_model_reference'],
             'visual_description':'同一服装、同一发型。','voiceover_en':'Explore the product details.'}]}}
    def apply(self,d):return normalize_and_guard(d,[{'id':'identity','text':'Synthetic product'}])
    def test_cta_mapping_preserves_original_and_other_fields(self):
        d=self.draft();before=copy.deepcopy(d);out=self.apply(d)
        self.assertEqual(d,before);self.assertEqual(out['storyboard']['shots'][-1]['visual_evidence'],'cta_product')
        self.assertEqual(out['storyboard']['shots'][0],before['storyboard']['shots'][0])
    def test_retention_and_package_assertions_rejected(self):
        for line in ['Ruby Red keeps it red.','The box lists three starting shades.','This color lasts longer.']:
            with self.subTest(line=line):
                d=self.draft();d['storyboard']['shots'][0]['voiceover_en']=line
                with self.assertRaisesRegex(ValidationFailure,'STRUCTURE_INVALID'):self.apply(d)
    def test_visualization_cannot_include_packaging_asset_or_affirmative_text(self):
        for key,val in [('asset_references',['packaging_front']),('visual_description','人物持一盒包装。'),('visual_description','人物持盒，另一只手不动。')]:
            d=self.draft();d['storyboard']['shots'][0][key]=val
            with self.assertRaises(ValidationFailure) as e:self.apply(d)
            self.assertEqual(e.exception.code,'VISUALIZATION_PACKAGE_FORBIDDEN')
    def test_last_shot_relabel_does_not_bypass_visualization_check(self):
        d=self.draft();d['storyboard']['shots'][-1].update(visual_evidence='generated_visualization',asset_references=['packaging_front'])
        with self.assertRaises(ValidationFailure):self.apply(d)
    def test_only_authorized_claim_ids(self):
        d=self.draft();d['goal_answer']['claim_ids']=['invented']
        with self.assertRaises(ValidationFailure) as e:self.apply(d)
        self.assertEqual(e.exception.field,'goal_answer.claim_ids')
    def test_evidence_decision_remains_evidence_not_a_fabricated_draft(self):
        d={'decision':'needs_evidence','missing_facts':['Synthetic unknown']}
        self.assertEqual(self.apply(d),d)
    def test_constraints_are_hashed_upstream_not_mutated_in_place(self):
        messages=[{'role':'system','content':'Keep exact Hook.'}];out=strengthen_messages(messages)
        self.assertEqual(messages[0]['content'],'Keep exact Hook.')
        self.assertIn('approved_claims',out[0]['content']);self.assertIn('ONE outfit',RULES)
        self.assertIn('hairstyle unchanged',RULES);self.assertIn('Keep exact Hook.',out[0]['content'])
