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

    def test_cta_exception_two_valid_forms_but_no_nonfinal_label_escape(self):
        for visual in ['人物正面对镜手持一盒闭合包装，开口说话，口型同步。',
                       '产品单独闭合包装静置，画外音，人物和手均不入镜。',
                       'Product-only closed carton rotating, voiceover.']:
            d=self.draft();d['storyboard']['shots'][-1].update(visual_evidence='generated_visualization',asset_references=['packaging_front'],visual_description=visual)
            self.assertEqual(self.apply(d)['storyboard']['shots'][-1]['visual_evidence'],'cta_product')
        for role in ['generated_context','product_package','cta_product']:
            d=self.draft();d['storyboard']['shots'][0].update(visual_evidence=role,visual_description='头发是生成效果示意，人物手持闭合包装。')
            with self.assertRaises(ValidationFailure) as e:self.apply(d)
            self.assertEqual(e.exception.code,'VISUALIZATION_PACKAGE_FORBIDDEN')
    def test_cta_package_open_or_back_view_or_mixed_staging_denied(self):
        for visual in ['背影人物持闭合包装，旁白。','产品单独闭合包装旋转，人物手持，画外音。',
                       '正面人物持包装开口说话。','产品单独闭合包装静置，打开盒子，画外音。']:
            d=self.draft();d['storyboard']['shots'][-1].update(asset_references=['packaging_front'],visual_description=visual)
            with self.assertRaises(ValidationFailure) as e:self.apply(d)
            self.assertEqual(e.exception.code,'CTA_PACKAGE_STAGING_INVALID')
    def test_cta_context_and_batch_rotation_no_change_to_factual_claims(self):
        from video_factory.script_content_policy import plan_cta_batch,SOFT_CTAS,DIRECT_CTAS
        items=[{'id':str(i),'ending_context':'shade_styling' if i<8 else 'product_display'} for i in range(16)]
        picks=plan_cta_batch(items)
        from collections import Counter
        self.assertEqual(set(list(picks.values())[:8]),set(SOFT_CTAS))
        self.assertEqual(set(list(picks.values())[8:]),set(DIRECT_CTAS))
        self.assertEqual(set(Counter(picks.values()).values()),{2})
        self.assertEqual(plan_cta_batch(items),picks)
        self.assertNotEqual(plan_cta_batch(items[:1],recent=['Check the shade.'])['0'],'Check the shade.')
    def test_preparation_binds_cta_into_provider_snapshot_not_just_documentation(self):
        from video_factory.script_content_policy import prepare_policy_batch
        from video_factory.prepared_script_provider import PreparedScriptProvider
        samples=[{'sample_id':str(i),'messages':[{'role':'system','content':'Exact Hook unchanged.'},{'role':'user','content':'{}'}]} for i in range(8)]
        before=copy.deepcopy(samples)
        compiled=prepare_policy_batch(samples,{str(i):'held_package' for i in range(8)})
        provider=PreparedScriptProvider(compiled,lambda *args:None)
        self.assertEqual(samples,before);self.assertEqual(len(provider.samples),8)
        for s in compiled.values():
            self.assertIn(s['preferred_cta'],s['messages'][-1]['content'])
            self.assertIn('Exact Hook unchanged.',s['messages'][0]['content'])

    def test_precompiled_inputs_are_rejected_instead_of_stacking_conflicting_rules(self):
        with self.assertRaisesRegex(ValueError,'UNCOMPILED_INPUT_REQUIRED'):
            strengthen_messages(strengthen_messages([{'role':'system','content':'Original rule.'}]))

    def test_exact_packaging_quote_allowed_only_for_bound_sku(self):
        claims=[{'id':'identity','text':'Synthetic'},
                {'id':'p','kind':'package_verbatim','text':'Ultra Long-Lasting'}]
        for line in ['Ultra Long-Lasting.', 'The box says "Ultra Long-Lasting".']:
            d=self.draft();d['storyboard']['shots'][0]['voiceover_en']=line
            normalize_and_guard(d,claims)
            with self.assertRaises(ValidationFailure):self.apply(d)
        for line in ['Permanent Hair Color.', 'Intense Hair Color.', 'Permanent Color.']:
            d=self.draft();d['storyboard']['shots'][0]['voiceover_en']=line
            with self.assertRaises(ValidationFailure) as e:normalize_and_guard(d,claims)
            self.assertEqual(e.exception.code,'UNAPPROVED_PACKAGE_QUOTE')

    def test_quote_exceptions_never_allow_forbidden_words_or_extra_guarantee(self):
        claims=[{'id':'identity'}, {'id':'p','kind':'package_verbatim','text':'Ultra Long-Lasting'}]
        for line in ['Perfectly Cover Gray Hair', 'Restore Your Damaged Hair', 'Healthy Look',
                     'Ultra Long-Lasting for six weeks.', 'Guaranteed Ultra Long-Lasting.',
                     'Ultra Long-Lasting keeps it red.', 'Ultra Long-Lasting never fades.']:
            for key in ['voiceover_en','on_screen_text_en','visual_description'] if line=='Healthy Look' else ['voiceover_en']:
                d=self.draft();d['storyboard']['shots'][0][key]=line
                with self.assertRaises(ValidationFailure):normalize_and_guard(d,claims)

    def test_box_summary_attribution_and_exact_text_required(self):
        summary='The box photo shows a muted copper shade.'
        claims=[{'id':'identity'}, {'id':'tone','kind':'box_photo_summary','text':summary}]
        d=self.draft();d['storyboard']['shots'][0]['voiceover_en']=summary
        normalize_and_guard(d,claims)
        for line in ['Your hair becomes a muted copper shade.',
                     'The box photo shows a brighter copper shade.']:
            d['storyboard']['shots'][0]['voiceover_en']=line
            with self.assertRaises(ValidationFailure):normalize_and_guard(d,claims)
