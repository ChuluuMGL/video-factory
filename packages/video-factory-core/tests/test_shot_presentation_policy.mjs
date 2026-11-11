import assert from 'node:assert/strict';
import {legacyMouthConflict as check} from '../src/video_factory/validation_assets/shot_presentation_policy.mjs';
for(const v of [
 '背影镜头，头发自然转动。嘴部不入镜；画外音继续。',
 'Hair close-up, no visible mouth; voiceover continues.',
 'Product-only static closed carton, no visible mouth; voiceover.',
 '正面对镜开口说话，完整面部与口型同步。'
])assert.equal(check(v),false,v);
for(const v of [
 'Front-facing creator speaks; no visible mouth; voiceover.',
 '正面对镜开口说话，但不露嘴。',
 'Back view; no visible mouth; all speech is voiceover.',
 '全片不露嘴，背影配旁白。',
 'No visible mouth.',
 '背影，不露嘴。'
])assert.equal(check(v),true,v);
console.log('10 synthetic presentation cases passed');
