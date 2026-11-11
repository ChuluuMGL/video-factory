"""Bind human-approved per-SKU facts into new immutable script inputs only."""
import copy
import hashlib
import re

from .runtime_store import canonical


def bind_approved_catalog(prepared, payload, catalog, *, sku, catalog_sha256):
    """Caller must provide a trusted approval digest, never one from model output.

    Bind both the model payload and full-validator binding. Returned copies must
    be persisted and hashed as NEW snapshots; old receipts are never rewritten.
    Source identity matching is exact: no fuzzy SKU or cross-product fallback.
    """
    if hashlib.sha256(canonical(catalog).encode()).hexdigest()!=catalog_sha256:
        raise ValueError('CLAIMS_CATALOG_CHANGED')
    if catalog.get('status')!='approved' or not catalog.get('approval_reference'):
        raise ValueError('CLAIMS_CONFIRMATION_REQUIRED')
    entries=[e for e in catalog.get('items',[]) if e.get('sku')==sku]
    if len(entries)!=1:raise ValueError('EXACT_SKU_CLAIMS_REQUIRED')
    item=entries[0]
    if item.get('approval_status')!='approved':raise ValueError('CLAIMS_CONFIRMATION_REQUIRED')
    claims=item.get('approved_claims')
    if not isinstance(claims,list) or not claims:raise ValueError('CLAIMS_REQUIRED')
    if len({c.get('id') for c in claims})!=len(claims):raise ValueError('CLAIM_ID_DUPLICATE')
    for c in claims:
        if (not c.get('id') or not c.get('text') or c.get('sku')!=sku
                or c.get('kind') not in ('sku_identity','package_verbatim','box_photo_summary')
                or not re.fullmatch(r'[0-9a-f]{64}',c.get('source_sha256',''))
                or not c.get('source_location')):
            raise ValueError('CLAIM_PROVENANCE_REQUIRED')
        if c['kind']=='box_photo_summary' and not c['text'].startswith('The box photo shows '):
            raise ValueError('BOX_PHOTO_ATTRIBUTION_REQUIRED')
    left,right=copy.deepcopy(prepared),copy.deepcopy(payload)
    if left['input']['binding'].get('sku_name')!=sku or right.get('sku_name')!=sku:
        raise ValueError('EXACT_SKU_BINDING_REQUIRED')
    if 'claims_catalog_sha256' in left['input']['binding'] or 'claims_catalog_sha256' in right:
        raise ValueError('UNCOMPILED_CLAIMS_INPUT_REQUIRED')
    for target in (left['input']['binding'],right):
        target['approved_claims']=copy.deepcopy(claims)
        target['claims_catalog_sha256']=catalog_sha256
        target['claims_catalog_version']=catalog['version']
    return left,right
