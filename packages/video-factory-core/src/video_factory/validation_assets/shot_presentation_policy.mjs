// Pure presentation policy. No model, filesystem or production access.
export function legacyMouthConflict(visual){
 const v=String(visual??'');
 const hidden=/嘴部不入镜|不露嘴|避开嘴部|无可见嘴|no (?:visible|speaking) mouth|no lip.sync|all speech is voiceover/i.test(v);
 if(!hidden)return false;
 if(/all speech is voiceover|全片.{0,8}(?:不露嘴|旁白)|全程.{0,8}(?:不露嘴|旁白)/i.test(v))return true;
 const positive=v.split(/[，,。；;\n]/).filter(c=>!/^\s*(?:不要|禁止|不得|严禁|无|不|never\b|do not\b|don't\b|no\b)/i.test(c.trim())).join(';');
 const front=/正面讲解|正面对镜|正面.{0,20}(?:开口|讲话|说话)|(?:面对镜头|对镜头|看向镜头).{0,8}(?:说|开口)|front.facing|direct.camera|facing the camera/i.test(positive);
 const detail=/背影|背面|后脑|头发特写|发丝特写|产品单独|仅产品|back.view|rear.view|back of (?:her |the )?head|hair.detail|hair close.up|product.only|product alone/i.test(positive);
 const voiceover=/画外音|旁白|voice.?over|off.camera/i.test(positive);
 return front || !(detail && voiceover);
}
