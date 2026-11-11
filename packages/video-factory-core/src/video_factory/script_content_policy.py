"""Opt-in copy-trial content constraints and deterministic evidence labels.

These guards supplement, never replace, the existing full script validator.
They do not certify factual correctness or grant human review approval.
"""
import copy
import re
from .script_receipts import ValidationFailure

VERSION = 'approved-claims-cta-continuity-v2'
RULES = (
    'Product facts in dialogue, captions and visuals MUST come only from approved_claims. '
    'Identity-only claims permit identity only. Never infer facts from package artwork, '
    'prior scripts or tone images. No efficacy, coverage or color-retention promises '
    '(including "keeps it red"), personal-use results, or unauthorized package contents. '
    'Use ordinary preference and invitation language instead. If the fixed task cannot '
    'be answered without an unsupported fact, return needs_evidence; never invent it. '
    'Outside the final CTA, ANY generated hair visualization MUST exclude packaging, '
    'regardless of its evidence label. The last shot MUST be cta_product. A CTA may '
    'show packaging only in either of two modes: (1) front-facing creator holding '
    'ONE closed carton and speaking the CTA with synchronized lips, or (2) closed '
    'product alone, static or rotating, with voiceover and no person or hands. '
    'Do not show opening, contents or application. Do not repeat a product introduction. '
    'Soft shade/styling invitations and direct buying CTAs are both allowed. Prefer '
    'soft wording for shade/styling topics, direct wording for product display or '
    'held-package endings; vary endings within the batch. Never add an unsupported promise. '
    'Back views, hair details and product-only views may use voiceover without a visible '
    'mouth; front-facing speaking shots must still show synchronized lips. '
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
    """CTA exception is positional; relabeling a non-final shot cannot bypass it."""
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
        assets=shot.get('asset_references',[])
        text=str(shot.get('visual_description',''))
        # Ignore explicit absence/prohibition clauses, not affirmative holding.
        text=re.sub(r'(?:不(?:得|能|要)?|没有|无|禁止)(?:出现|展示|持|拿|任何|产品|闭合|的|额外|[ ，、]){0,12}(?:包装|盒)(?:动作)?','',text)
        text=re.sub(r'\b(?:no|without|never show|do not hold)\s+(?:visible\s+|any\s+|a\s+|holding\s+)?(?:box|package|packaging)\b','',text,flags=re.I)
        package=(any(re.search(r'packag|box|carton',str(a),re.I) for a in assets)
                 or re.search(r'包装|盒|\b(?:box|package|packaging|carton)\b',text,re.I))
        visualization=(shot.get('visual_evidence')=='generated_visualization'
                       or re.search(r'生成.{0,12}(?:发色|头发)|(?:发色|头发).{0,12}(?:示意|生成)|generated.{0,30}hair|hair.{0,30}(?:visualization|illustration)',text,re.I))
        if package and i<len(shots)-1 and visualization:
            raise ValidationFailure(field,'VISUALIZATION_PACKAGE_FORBIDDEN',
                                    'Non-CTA generated hair visualization must be package-free, regardless of label')
        if package and i==len(shots)-1:
            closed=bool(re.search(r'闭合|未开封|closed|sealed',text,re.I))
            held=bool(re.search(r'持盒|手持|拿着|holding|holds',text,re.I))
            front=bool(re.search(r'正面|面对镜头|front.facing|facing the camera',text,re.I))
            speaking=bool(re.search(r'开口|口型.{0,8}同步|speaks?|synchronized lips|lip.sync',text,re.I))
            alone=bool(re.search(r'产品单独|仅产品|产品独立|product.only|product alone|carton alone',text,re.I))
            motion=bool(re.search(r'静置|静态|旋转|static|stationary|rotat',text,re.I))
            vo=bool(re.search(r'画外音|旁白|voiceover|voice.over|off.camera voice',text,re.I))
            positive=';'.join(c for c in re.split(r'[，,。；;\n]',text) if not re.match(r'\s*(?:不要|禁止|不得|严禁|无|不|never\b|do not\b|no\b)',c,re.I))
            forbidden=bool(re.search(r'打开|开盒|展开|倒出|涂抹|unbox|open (?:the )?(?:box|carton)|apply product',positive,re.I))
            if not closed or forbidden or not ((held and front and speaking and not alone) or (alone and motion and vo and not held)):
                raise ValidationFailure(field,'CTA_PACKAGE_STAGING_INVALID',
                    'CTA package must be closed: front-facing holder speaking, or product-only static/rotation with voiceover')
    shots[-1]['visual_evidence']='cta_product'
    return result


SOFT_CTAS=('Check the shade.','Explore this shade.','Find your shade match.','See the color details.')
DIRECT_CTAS=('Shop now.','Tap below to shop.','Shop this shade.','Find it in the shop.')


def plan_cta_batch(items, recent=()):
    """Stable input planning, not a post-generation text rewrite or claim approval.

    Each item supplies a unique id and reviewed ending context. Persist choices
    in the immutable prepared input; a retry must reuse the same choice.
    """
    from collections import Counter
    counts=Counter(str(x).strip().casefold() for x in recent)
    result={}
    for item in items:
        name=item['id'];kind=item['ending_context']
        if name in result or kind not in ('shade_styling','product_display','held_package'):
            raise ValueError('CTA_BATCH_CONTEXT_INVALID')
        pool=SOFT_CTAS if kind=='shade_styling' else DIRECT_CTAS
        chosen=min(pool,key=lambda v:counts[v.casefold()])
        result[name]=chosen;counts[chosen.casefold()]+=1
    return result
