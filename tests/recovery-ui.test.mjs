import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
const selected=new URL('../recovery.html', import.meta.url);
assert.ok(selected,'explicit candidate path required');
const html=fs.readFileSync(selected,'utf8'),code=html.match(/<script>\s*([\s\S]+?)<\/script>/)[1];
const bytes=new Uint8Array([137,80,78,71,13,10,26,10]); // HTTP-boundary-only fixture, not valid image evidence.
const digest=Buffer.from(await webcrypto.subtle.digest('SHA-256',bytes)).toString('hex');
const oid='AS-OX-REVIEW-ONLY',token='A'.repeat(43),uuid='12345678-1234-4123-8123-123456789abc';
async function page(error=null){
 const elements={},calls=[];
 const element=()=>({hidden:true,disabled:true,files:[],value:'',textContent:'',classList:{toggle(){}},handlers:{},addEventListener(k,fn){this.handlers[k]=fn;},replaceChildren(){},append(){}});
 const $=id=>elements[id]||=(element());
 const saved={id:uuid,status:'ready',sha256:digest,byte_size:bytes.length,mime_type:'image/png'};
 const sandbox={document:{getElementById:$,createElement:element},location:{hash:'#order='+oid+'&token='+token},URLSearchParams,crypto:webcrypto,fetch:async(url,opts)=>{
  const action=url.split('/').at(-1);calls.push({action,opts,url});
  assert.ok(!url.includes(token));assert.equal(opts.headers.Authorization,'Bearer '+token);assert.equal(opts.headers['X-Anthem-Order'],oid);assert.equal(opts.credentials,'omit');
  if(action==='status')return new Response(JSON.stringify({order_id:oid,status:'awaiting_sources',files:[saved]}));
  if(action==='upload')return new Response(JSON.stringify(error?{error}:{id:uuid,status:'ready'}),{status:error?409:200});
  throw Error('Unexpected operation '+action);
 }};
 vm.runInNewContext(code,sandbox);await new Promise(r=>setImmediate(r));
 $('photos').files=[{size:bytes.length,type:'image/png',arrayBuffer:async()=>bytes.buffer}];
 await $('upload').handlers.click();return {elements,calls};
}
test('reselecting legacy saved bytes revalidates existing UUID instead of skipping',async()=>{
 const {calls}=await page();const uploads=calls.filter(c=>c.action==='upload');assert.equal(uploads.length,1,'saved image was never revalidated');assert.equal(uploads[0].opts.headers['X-Upload-Id'],uuid);
 assert.ok(!calls.some(c=>c.action==='submit'),'upload cannot auto-submit or dispatch');
});
for(const [error,message] of [['source_validation_required','Select your photos again'],['image_decode_timeout','took too long'],['image_too_large','25 megapixels'],['upload_busy_retry','Another upload']])test('actionable recovery for '+error,async()=>{
 const {elements,calls}=await page(error);assert.ok(elements.message.textContent.includes(message),elements.message.textContent);assert.equal(elements.upload.disabled,false,'same-image retry should remain usable');assert.equal(calls.filter(c=>c.action==='upload').length,1);
});
