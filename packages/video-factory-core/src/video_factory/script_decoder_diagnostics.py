"""Extract a validator's bounded diagnostic without retaining stack/source text."""
import re
from .runtime_store import RuntimeFault
from .script_receipts import ValidationFailure

FIELDS={
    'MODEL_BINDING_DRIFT':'binding', 'MISSING_EVIDENCE_DETAILS':'missing_facts',
    'MISSING_DRAFT':'decision/storyboard.shots', 'SCRIPT_PRESENTATION_REVIEW_REQUIRED':'storyboard.shots',
    'SCRIPT_QUALITY_FAILED':'storyboard.shots', 'SHOT_COUNT_OUTSIDE_3_TO_8':'storyboard.shots',
    'APPROVED_HOOK_DRIFT':'storyboard.shots[0].voiceover_en/end_seconds',
    'PAIRED_BODY_DRIFT_REQUIRES_PAIR_REVISION':'storyboard.shots[1:]',
    'STORYBOARD_FAILED':'storyboard.shots', 'UNAVAILABLE_ASSET':'asset_references',
    'VO_CAPTION_MISMATCH':'voiceover_en/on_screen_text_en', 'MISSING_SHOT_VISUAL':'visual_description',
    'MISSING_GOAL_ANSWER':'goal_answer', 'UNSUPPORTED_PRODUCT_CLAIM':'goal_answer.claim_ids',
    'GOAL_ANSWER_SHOT_MISSING':'goal_answer.shot_numbers', 'UNRESOLVED_FEEDBACK':'feedback_resolution',
    'FEEDBACK_CHANGE_NOT_PROVEN':'feedback_resolution', 'REPAIR_NOT_CHANGED':'storyboard/employee_feedback_resolution',
    'FEEDBACK_COPY_NOT_CHANGED':'caption_voiceover_script', 'MODEL_REFERENCE_MISSING':'asset_references',
    'EXTRA_GRAPHIC_TEXT_OUTSIDE_CAPTION':'visual_description',
    'GOAL_EVIDENCE_MISSING_RETURN_NEEDS_EVIDENCE':'decision', 'TIMELINE_ALLOCATION':'storyboard.shots',
}


def validator_failure(stderr):
    """Only known validation errors may continue; runtime/Node faults stop all."""
    text=stderr.decode('utf-8','replace') if isinstance(stderr,bytes) else str(stderr)
    match=re.search(r'^Error: ([A-Z][A-Z0-9_]*)(?::([^\n]*))?\s*$',text,re.M)
    if match and match[1] in FIELDS:
        code,detail=match[1],(match[2] or '').strip();field=FIELDS[code]
        if code=='MODEL_BINDING_DRIFT' and detail in ('plan_version','source_script_version','goal'):field=detail
        elif code in ('UNAVAILABLE_ASSET','VO_CAPTION_MISMATCH','MISSING_SHOT_VISUAL','MODEL_REFERENCE_MISSING','EXTRA_GRAPHIC_TEXT_OUTSIDE_CAPTION') and re.fullmatch(r'\d{1,2}',detail):
            field='storyboard.shots[shot_no='+detail+'].'+field
        return ValidationFailure(field,code,detail or code)
    if re.search(r'^SyntaxError: .*JSON',text,re.M):
        return ValidationFailure('$','INVALID_MODEL_JSON','Model content is not valid JSON')
    raise RuntimeFault('SCRIPT_DECODER_FAILED')
