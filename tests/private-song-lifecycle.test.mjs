import test from 'node:test';
import assert from 'node:assert/strict';
import {initialize} from '../private-song.js';
const token='A'.repeat(43),order='AS-PRIVATE-LIFECYCLE';
function env(){
 const events={},created=[],revoked=[],calls=[];
 const elements=Object.fromEntries(['status','player','audio','download','retry'].map(id=>[id,{
  hidden:true,disabled:true,dataset:{},textContent:'',events:{},duration:204,
  addEventListener(k,f){this.events[k]=f;},removeAttribute(k){delete this[k];},pause(){},load(){}
 }]));
 return {elements,events,created,revoked,calls,document:{getElementById:id=>elements[id]},
 location:{hash:'#order='+order+'&token='+token,pathname:'/private-song.html'},history:{replaceState(){}},
 lifecycle:{addEventListener(k,f){events[k]=f;}},urlAPI:{createObjectURL(b){created.push(b);return 'blob:fixture';},revokeObjectURL(u){revoked.push(u);}},
 fetchImpl:async(url,options)=>{calls.push({url,options});return url.endsWith('/status')?
 new Response(JSON.stringify({status:'ready',byte_size:4,mime_type:'audio/mpeg',duration_seconds:204})):
 new Response(new Uint8Array([1,2,3,4]),{headers:{'Content-Type':'audio/mpeg'}});}};
}
test('no ready/download before decoder event, then full-duration canplay enables',async()=>{
 const e=env();await initialize(e);assert.equal(e.elements.download.disabled,true);assert.doesNotMatch(e.elements.status.textContent,/full song is ready/);
 e.elements.audio.oncanplay();assert.equal(e.elements.download.disabled,false);assert.match(e.elements.status.textContent,/full song is ready/);e.events.pagehide();
});
test('pagehide during pending fetch aborts and blocks late Blob creation',async()=>{
 const e=env();let release;const pending=new Promise(r=>{release=r;});const fetch=e.fetchImpl;
 e.fetchImpl=async(...args)=>{await pending;return fetch(...args);};const running=initialize(e);
 assert.equal(typeof e.events.pagehide,'function');e.events.pagehide();release();await running;
 assert.equal(e.created.length,0);assert.equal(e.elements.download.disabled,true);assert.equal(e.calls[0].options.signal.aborted,true);
});
test('corrupt audio disables download, revokes bytes, retry only GETs same capability',async()=>{
 const e=env();await initialize(e);e.elements.audio.onerror();assert.equal(e.elements.download.disabled,true);assert.equal(e.elements.player.hidden,true);assert.deepEqual(e.revoked,['blob:fixture']);assert.match(e.elements.status.textContent,/Do not pay again/);
 await e.elements.retry.events.click();e.elements.audio.oncanplay();assert.equal(e.elements.download.disabled,false);
 assert.equal(e.calls.length,4);for(const c of e.calls){assert.equal(c.options.method,'GET');assert.equal(c.options.headers.Authorization,'Bearer '+token);}e.events.pagehide();
});
for(const duration of [1,179,241,NaN,Infinity])test('invalid full duration rejected: '+duration,async()=>{
 const e=env();await initialize(e);e.elements.audio.duration=duration;e.elements.audio.onloadedmetadata();e.elements.audio.oncanplay();assert.equal(e.elements.download.disabled,true);assert.equal(e.elements.player.hidden,true);e.events.pagehide();
});
test('stale media events and duplicate retries cannot supersede a fresh load',async()=>{
 const e=env();await initialize(e);const stale=e.elements.audio.oncanplay;e.elements.audio.onerror();
 const loading=e.elements.retry.events.click();const double=e.elements.retry.events.click();await Promise.all([loading,double]);
 stale();assert.equal(e.elements.download.disabled,true);assert.equal(e.calls.length,4);e.elements.audio.oncanplay();assert.equal(e.elements.download.disabled,false);e.events.pagehide();
});
test('pagehide after ready revokes and fences late media events',async()=>{
 const e=env();await initialize(e);e.elements.audio.oncanplay();e.events.pagehide();e.elements.audio.oncanplay();assert.equal(e.elements.download.disabled,true);assert.equal(e.elements.player.hidden,true);assert.deepEqual(e.revoked,['blob:fixture']);
});
