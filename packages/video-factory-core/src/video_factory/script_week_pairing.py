"""Immutable input/baseline pairing. Never infers provenance from cell equality.

The adapter supplies a stable, projected source bundle and an approved plan.
Production output is separate from generation input. A baseline is comparable
only when its original execution receipt proves the same input and outputs.
"""
import copy
import re
from .runtime_store import RuntimeFault, fingerprint
from .script_week_archive import OUTPUTS


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def freeze_inputs(linked, document, *, approved_document, approved_revision,
                  approved_content_sha256, compiler_sha256, claims_sha256):
    if (not isinstance(linked, dict) or set(linked) != {'inputs','input_sha256','provenance'}
            or fingerprint(linked['inputs']) != linked['input_sha256']):
        raise RuntimeFault('WEEK_INPUT_DIGEST_MISMATCH')
    if (not isinstance(document, dict) or set(document) != {'document_id','revision','content'}
            or document['document_id'] != approved_document
            or str(document['revision']) != str(approved_revision)
            or not isinstance(document['content'], str)):
        raise RuntimeFault('WEEK_PLAN_APPROVAL_CHANGED')
    import hashlib
    content_sha = hashlib.sha256(document['content'].encode()).hexdigest()
    if (content_sha != approved_content_sha256
            or not all(_hash(v) for v in (compiler_sha256, claims_sha256))):
        raise RuntimeFault('WEEK_PLAN_OR_POLICY_CHANGED')
    shared = {'inputs':copy.deepcopy(linked['inputs']),
              'plan':{'document_id':approved_document, 'revision':str(approved_revision),
                      'content_sha256':content_sha}}
    # Policy changes do not change the shared production input. Both hashes are
    # retained so an improved candidate policy cannot masquerade as n8n policy.
    return {'shared_input':shared, 'shared_input_sha256':fingerprint(shared),
            'candidate_policy':{'compiler_sha256':compiler_sha256,'claims_sha256':claims_sha256},
            'provenance':copy.deepcopy(linked['provenance'])}


def pair_baseline(frozen, receipt, visible_outputs):
    """Receipt must come from the configured readonly production evidence reader.

    Missing or manually edited provenance remains unpaired, even when visible
    strings happen to match. No paid retry or production write is performed.
    """
    def no(reason):return {'status':'unpaired_or_pending','reason':reason,'outputs':dict.fromkeys(OUTPUTS)}
    if not receipt:return no('production_execution_receipt_missing')
    if receipt.get('producer') != 'n8n' or receipt.get('unmodified') is not True:
        return no('not_unmodified_n8n_output')
    if receipt.get('shared_input_sha256') != frozen['shared_input_sha256']:
        return no('input_version_mismatch')
    source = frozen['provenance']
    if any(receipt.get(k) != source.get(k) for k in ('base','task_table','task_record')):
        return no('source_identity_mismatch')
    outputs = receipt.get('outputs')
    if (not isinstance(outputs, dict) or set(outputs) != set(OUTPUTS)
            or any(not isinstance(v,str) or not v.strip() for v in outputs.values())
            or receipt.get('outputs_sha256') != fingerprint(outputs)):
        return no('output_receipt_invalid')
    if outputs != visible_outputs:return no('visible_outputs_changed')
    if not _hash(receipt.get('execution_receipt_sha256')):
        return no('execution_receipt_digest_missing')
    return {'status':'matched','reason':None,'outputs':copy.deepcopy(outputs),
            'execution_receipt_sha256':receipt['execution_receipt_sha256'],
            'shared_input_sha256':frozen['shared_input_sha256']}
