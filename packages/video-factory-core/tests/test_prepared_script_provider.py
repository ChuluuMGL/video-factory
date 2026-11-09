import copy
import hashlib
import json
import unittest
from unittest.mock import MagicMock, patch
from video_factory.prepared_script_provider import PreparedScriptProvider
from video_factory.runtime_store import RuntimeFault, canonical, fingerprint


class PreparedScriptProviderTests(unittest.TestCase):
    def setUp(self):
        self.sku={'sku_id':'synthetic','facts':'synthetic facts'}
        messages=[{'role':'system','content':'Return structured draft only.'},
                  {'role':'user','content':'Synthetic approved brief.'}]
        self.sample={'messages':messages,'messages_sha256':hashlib.sha256(canonical(messages).encode()).hexdigest(),
                     'sku_sha256':fingerprint(self.sku),'feedback_sha256':fingerprint(''),
                     'source_revision':'synthetic-v1'}
        self.ref='snapshot:'+fingerprint(self.sample)
        self.fields={'video_script':'draft','caption_voiceover_script':'voice','generation_prompt':'prompt'}

    def provider(self,decoder=None,sample=None):
        return PreparedScriptProvider({self.ref:sample or self.sample},decoder or (lambda s,r:json.loads(r['content'])))

    def response(self,fields=None,reason='stop'):
        response=MagicMock();response.__enter__.return_value=response;response.status=200
        response.read.return_value=json.dumps({'id':'synthetic-receipt',
            'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120},
            'choices':[{'finish_reason':reason,'message':{'content':json.dumps(self.fields if fields is None else fields)}}]}).encode()
        return response

    def test_pinned_input_exactly_once_fixed_origin_and_caps(self):
        with patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.return_value=self.response()
            result=self.provider().generate(self.sku,self.ref,'','synthetic-key')
            req=opener.return_value.open.call_args.args[0];body=json.loads(req.data)
            self.assertEqual(opener.return_value.open.call_count,1)
            self.assertEqual(req.full_url,'https://api.deepseek.com/chat/completions')
            self.assertEqual(body['messages'],self.sample['messages']);self.assertEqual(body['max_tokens'],2048)
            self.assertEqual(result['structured_fields'],self.fields);self.assertEqual(result['script'],'draft')
            self.assertNotIn('synthetic-key',str(result))

    def test_changed_snapshot_digest_and_source_version_denied(self):
        for key,value in [('source_revision','changed'),('messages_sha256','0'*64)]:
            changed=copy.deepcopy(self.sample);changed[key]=value
            with self.assertRaisesRegex(RuntimeFault,'DIGEST_MISMATCH'):self.provider(sample=changed)

    def test_wrong_task_sku_feedback_and_unregistered_path_denied_before_network(self):
        with patch('video_factory.script_provider.build_opener') as opener:
            for sku,brief,feedback in [({},self.ref,''),(self.sku,'/etc/passwd',''),(self.sku,self.ref,'changed')]:
                with self.assertRaisesRegex(RuntimeFault,'BINDING_MISMATCH'):
                    self.provider().generate(sku,brief,feedback,'synthetic-key')
            opener.assert_not_called()

    def test_mutating_external_registry_cannot_change_pinned_request(self):
        sample=copy.deepcopy(self.sample);provider=self.provider(sample=sample)
        sample['messages'][1]['content']='changed'
        self.assertEqual(provider.samples[self.ref]['messages'],self.sample['messages'])

    def test_malformed_fields_and_truncation_never_review_ready_or_retried(self):
        for fields,reason in [({},'stop'),({**self.fields,'extra':'not allowed'},'stop'),
                              ({**self.fields,'video_script':'x'*6501},'stop'),(self.fields,'length')]:
            with self.subTest(reason=reason),patch('video_factory.script_provider.build_opener') as opener:
                opener.return_value.open.return_value=self.response(fields,reason)
                with self.assertRaisesRegex(RuntimeFault,'SCRIPT_MODEL_TRUNCATED' if reason=='length' else 'SCRIPT_VALIDATION_FAILED'):
                    self.provider().generate(self.sku,self.ref,'','synthetic-key')
                self.assertEqual(opener.return_value.open.call_count,1)

    def test_decoder_exception_does_not_echo_private_payload(self):
        def decode(s,c):raise ValueError('private data')
        with patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.return_value=self.response()
            with self.assertRaisesRegex(RuntimeFault,'^SCRIPT_VALIDATION_FAILED$'):
                self.provider(decode).generate(self.sku,self.ref,'','synthetic-key')

    def test_decoder_receives_genuine_receipt_without_authentication_secret(self):
        receipts=[]
        def decode(sample,receipt):
            receipts.append(receipt)
            return json.loads(receipt['content'])
        with patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.return_value=self.response()
            self.provider(decode).generate(self.sku,self.ref,'','synthetic-key')
        self.assertEqual(receipts[0]['provider_id'],'synthetic-receipt')
        self.assertEqual(receipts[0]['usage']['total_tokens'],120)
        self.assertNotIn('synthetic-key',str(receipts))

    def test_transport_rejects_policy_override_before_network(self):
        with patch('video_factory.script_provider.build_opener') as opener:
            with self.assertRaisesRegex(RuntimeFault,'POLICY_DENIED'):
                self.provider()._submit({'model':'other','max_tokens':4000},'synthetic-key')
            opener.assert_not_called()

    def test_missing_facts_retains_receipt_without_pretending_a_draft(self):
        fields={'decision':'needs_evidence','missing_facts':['Specify the approved product size.']}
        with patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.return_value=self.response(fields)
            result=self.provider().generate(self.sku,self.ref,'','synthetic-key')
        self.assertEqual(result['decision'],'needs_evidence');self.assertNotIn('script',result)
        self.assertEqual(result['provider_id'],'synthetic-receipt');self.assertEqual(result['usage']['total_tokens'],120)
