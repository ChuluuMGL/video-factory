"""Pinned, private input snapshots for a copy-only structured script trial.

The registry and decoder are operator-supplied local configuration, never model
output. This provider does not grant authority: use it only behind ScriptJobs.
No filesystem paths or model-selected endpoint is accepted from a task brief.
"""
import copy
import hashlib
from .runtime_store import RuntimeFault, canonical, fingerprint
from .script_provider import ScriptProvider, MODEL


class PreparedScriptProvider(ScriptProvider):
    def __init__(self, samples, decoder):
        self.samples=copy.deepcopy(samples)
        self.decoder=decoder
        if not callable(decoder):raise RuntimeFault('SCRIPT_DECODER_REQUIRED')
        for ref,sample in self.samples.items():
            messages=sample.get('messages')
            if (not isinstance(messages,list) or not messages
                    or any(not isinstance(m,dict) or set(m)!={'role','content'}
                           or m['role'] not in ('system','user','assistant')
                           or not isinstance(m['content'],str) for m in messages)):
                raise RuntimeFault('SCRIPT_PREPARED_INPUT_INVALID')
            digest=hashlib.sha256(canonical(messages).encode()).hexdigest()
            if ref!='snapshot:'+fingerprint(sample) or sample.get('messages_sha256')!=digest:
                raise RuntimeFault('SCRIPT_PREPARED_DIGEST_MISMATCH')
            if len(canonical(messages).encode())>32768:raise RuntimeFault('SCRIPT_INPUT_LIMIT')

    def generate(self,sku,brief,feedback,secret):
        sample=self.samples.get(brief)
        if (not sample or fingerprint(sku)!=sample.get('sku_sha256')
                or fingerprint(feedback)!=sample.get('feedback_sha256')):
            raise RuntimeFault('SCRIPT_PREPARED_BINDING_MISMATCH')
        body={'model':MODEL,'stream':False,'max_tokens':2048,'thinking':{'type':'disabled'},
              'messages':copy.deepcopy(sample['messages']),
              'response_format':{'type':'json_object'},'temperature':0.4}
        result=self._submit(body,secret)
        # Validation failure preserves ScriptJobs' unknown intent and reservation.
        # It never retries and cannot label malformed output ready for review.
        try:
            fields=self.decoder(copy.deepcopy(sample),result['content'])
            required={'video_script','caption_voiceover_script','generation_prompt'}
            if not isinstance(fields,dict) or set(fields)!=required:raise ValueError
            for value in fields.values():
                if (not isinstance(value,str) or not value.strip()
                        or len(value.encode('utf-16-le'))//2>6500):raise ValueError
        except Exception:
            raise RuntimeFault('SCRIPT_SUBMISSION_UNCERTAIN_NO_RETRY') from None
        return {'script':fields['video_script'],'structured_fields':fields,
                'provider_id':result['provider_id'],'usage':result['usage'],
                'requested_model':MODEL,'prepared_input_sha256':sample['messages_sha256'],
                'prepared_source_revision':sample.get('source_revision')}
