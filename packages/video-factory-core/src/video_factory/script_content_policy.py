"""Opt-in copy-trial content constraints and deterministic evidence labels.

These guards supplement, never replace, the existing full script validator.
They do not certify factual correctness or grant human review approval.
"""
import copy
import re
from .script_receipts import ValidationFailure

VERSION = 'approved-claims-continuity-v1'
RULES = (
    'Product facts in dialogue, captions and visuals MUST come only from approved_claims. '
    'Identity-only claims permit identity only. Never infer facts from package artwork, '
    'prior scripts or tone images. No efficacy, coverage or color-retention promises '
    '(including "keeps it red"), personal-use results, or unauthorized package contents. '
    'Use ordinary preference and invitation language instead. If the fixed task cannot '
    'be answered without an unsupported fact, return needs_evidence; never invent it. '
    'Every generated_visualization shot MUST exclude packages, boxes and packaging assets. '
    'Use a separate product_package shot to show the closed package. The last shot MUST '
    'be cta_product, with a short next-step invitation and no repeated package introduction. '
    'Choose ONE outfit at the first shot and keep every garment, layer, neckline, sleeve '
    'and accessory unchanged throughout. Keep the approved face, hair length, texture, '
    'parting and hairstyle unchanged; no invented previously dyed segments. Hair color '
    'may change ONLY when the explicit approved scene calls for a labeled visualization. '
    'Repeat this continuity requirement in every visual_description. Existing exact Hook, '
    'duration, word counts, evidence and validation requirements remain mandatory.'
)


def strengthen_messages(messages):
    """Return new messages; caller must hash the new immutable input snapshot."""
    value=copy.deepcopy(messages)
    if not value or value[0].get('role')!='system':
        raise ValueError('SYSTEM_MESSAGE_REQUIRED')
    value[0]['content']+='\nCOPY TRIAL HARD CONSTRAINTS ('+VERSION+'): '+RULES
    return value


def normalize_and_guard(value, approved_claims):
    """Check the original labels before forcing CTA; never mask package violations."""
    result=copy.deepcopy(value)
    if result.get('decision')=='needs_evidence':return result
    shots=result.get('storyboard',{}).get('shots')
    if not isinstance(shots,list) or not shots:
        raise ValidationFailure('storyboard.shots','SHOTS_REQUIRED','Nonempty storyboard required')
    allowed={x.get('id') for x in approved_claims if isinstance(x,dict)}
    claims=result.get('goal_answer',{}).get('claim_ids',[])
    if not isinstance(claims,list) or any(c not in allowed for c in claims):
        raise ValidationFailure('goal_answer.claim_ids','UNAPPROVED_CLAIM','Claim IDs must be in approved_claims')
    for i,shot in enumerate(shots):
        field='storyboard.shots['+str(i)+']'
        speech=' '.join(str(shot.get(k,'')) for k in ('voiceover_en','on_screen_text_en'))
        if re.search(r'\b(?:keeps? (?:it |your (?:hair|colou?r) )?(?:red|vibrant|bright)|'
                     r'(?:colou?r|red) (?:lasts?|stays?)|fade[- ]?(?:proof|resistant)|'
                     r'(?:box|packag\w*) (?:lists?|says?|contains?|includes?)|'
                     r'(?:guarantee\w*|proven) (?:cover\w*|results?)|'
                     r'(?:covers?|eliminates?) (?:all |your )?(?:gr[ae]y|white))\b',speech,re.I):
            raise ValidationFailure(field+'.voiceover_en','UNAUTHORIZED_PRODUCT_PROMISE',
                                    'Efficacy, retention or package-fact assertion forbidden in this trial')
        if shot.get('visual_evidence')=='generated_visualization':
            assets=shot.get('asset_references',[])
            text=str(shot.get('visual_description',''))
            # Ignore explicit absence/prohibition clauses, not affirmative holding.
            text=re.sub(r'(?:不(?:得|能|要)?|没有|无|禁止)(?:出现|展示|持|拿|任何|产品|闭合|的|额外|[ ，、]){0,12}(?:包装|盒)(?:动作)?','',text)
            text=re.sub(r'\b(?:no|without|never show|do not hold)\s+(?:visible\s+|any\s+|a\s+|holding\s+)?(?:box|package|packaging)\b','',text,flags=re.I)
            if (any(re.search(r'packag|box',str(a),re.I) for a in assets)
                    or re.search(r'包装|盒|\b(?:box|package|packaging)\b',text,re.I)):
                raise ValidationFailure(field,'VISUALIZATION_PACKAGE_FORBIDDEN',
                                        'Generated visualization cannot show or reference packaging')
    shots[-1]['visual_evidence']='cta_product'
    return result
