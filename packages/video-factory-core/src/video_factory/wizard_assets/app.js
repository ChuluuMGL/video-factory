'use strict';
const $=id=>document.getElementById(id); let csrf='', current=null, busy=false, stopped=false;
async function request(path,body) {
 const r=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:body?{'Content-Type':'application/json','X-VF-CSRF':csrf}:{},body:body?JSON.stringify(body):undefined});
 if(!r.ok) throw new Error('连接已失效或提交未接受，请在 Agent 中查看安装状态。'); return r.json();
}
async function poll(){
 if(stopped)return;
 try {const v=await request('/api/prompt'); csrf=v.csrf; $('messages').textContent=v.messages.join('\n');
  if(v.prompt?.id!==current?.id){current=v.prompt; $('answer').value='';$('form').hidden=!current;$('quit').hidden=!current;
   if(current){$('label').textContent=current.label;$('answer').type=current.hidden?'password':'text';$('answer').focus();}}
  $('status').textContent=current?'请填写当前问题。':'正在执行，请稍候…';
 } catch(e){$('status').textContent=e.message;stopped=true;$('form').hidden=true;$('answer').value='';}
 if(!stopped)setTimeout(poll,800);
}
async function submit(answer){ if(busy||!current)return;busy=true;
 try {await request('/api/answer',{id:current.id,answer});$('answer').value='';$('form').hidden=true;}
 catch(e){$('status').textContent=e.message;} finally {busy=false;}
}
$('form').onsubmit=e=>{e.preventDefault();submit($('answer').value);}; $('quit').onclick=()=>submit(':quit');
(async()=>{try{let key=location.hash.slice(1);history.replaceState(null,'','/');if(key){await request('/api/unlock',{key});key='';}await poll();}catch(e){$('status').textContent=e.message;}})();
