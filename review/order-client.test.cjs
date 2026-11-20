/* Runs the actual HTML/app/client in a DOM with ALL networking intercepted.
 * Install jsdom for local verification. No paid generation or real checkout.
 */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {webcrypto}=require('node:crypto');
const {JSDOM}=require(process.env.JSDOM_PATH || 'jsdom');
const root=path.resolve(__dirname,'..');
const token='t'.repeat(48),oid='AS-12345678-abcd-efgh-1234';
const saved={order_id:oid,recovery_token:token};
const tick=()=>new Promise(r=>setImmediate(r));
async function environment({url='https://anthemsmith.com/',stored=null,tabOrder=null,status='awaiting_payment',handler=null,paused=false}={}) {
 const standalone=new URL(url).pathname.endsWith('/backend-song.html');
 const dom=new JSDOM(fs.readFileSync(path.join(root,standalone?'backend-song.html':'index.html'),'utf8'),{url,runScripts:'outside-only'}),w=dom.window;
 const calls=[],timers=new Map();let seq=0;
 w.setTimeout=(fn,delay)=>{timers.set(++seq,{fn,delay});return seq;};w.clearTimeout=id=>timers.delete(id);
 w.HTMLElement.prototype.scrollIntoView=function(){};
 w.HTMLDialogElement.prototype.close=function(){};w.HTMLDialogElement.prototype.showModal=function(){};
 w.URL.createObjectURL=()=> 'blob:fixture';w.URL.revokeObjectURL=()=>{};
 Object.defineProperty(w,'crypto',{value:webcrypto});
 w.File.prototype.arrayBuffer=function(){return new Promise((resolve,reject)=>{const r=new w.FileReader();r.onload=()=>resolve(new Uint8Array(r.result).buffer);r.onerror=reject;r.readAsArrayBuffer(this);});};
 w.HTMLMediaElement.prototype.load=function(){};
 if(tabOrder)w.sessionStorage.setItem('anthemsmith_private_'+tabOrder.order_id,JSON.stringify(tabOrder));
 if(stored)w.localStorage.setItem('anthemsmith_order_v2',JSON.stringify(stored));
 let current=status;
 w.fetch=async(url,init={})=>{
  assert.equal(new URL(url).origin,new URL(w.ANTHEMSMITH_API_BASE||'/api','https://anthemsmith.com').origin);
  calls.push({url,init});
  if(handler){const out=await handler(url,init,calls);if(out)return out;}
  let body;
  if(url.endsWith('/orders')&&init.method==='POST')body={...saved,status:'draft'};
  else if(url.endsWith('/checkout'))body={checkout_url:'https://checkout.stripe.com/c/pay/cs_test_fixture'};
  else if(url.endsWith('/details')){current='queued';body={order_id:oid,status:current};}
  else body={order_id:oid,status:current,audio_url:current==='ready'?'https://anthemsmith.com/private/signed-song.mp3':undefined,notification_status:'queued'};
  return {ok:true,status:200,json:async()=>body};
 };
 for(const name of standalone?['order-config.js']:['app.js','order-config.js'])w.eval(fs.readFileSync(path.join(root,name),'utf8'));
 // order-config ships the containment gate ON; these tests drive the flow, so state the build
 // they exercise explicitly instead of depending on the shipped default.
 w.ANTHEMSMITH_ORDERING_PAUSED=paused;
 w.eval(fs.readFileSync(path.join(root,'order-client.js'),'utf8'));
 await tick();
 const set=(name,value)=>{const el=w.document.querySelector(`[name="${name}"]`);el.value=value;return el;};
 const fill=()=>{set('instagram1','https://instagram.com/maya/');set('phone','(202) 555-0123');w.document.getElementById('consent_rights').checked=true;};
 const chooseFiles=(lane,count)=>{
  const radio=w.document.querySelector(`[name="intake_lane"][value="${lane}"]`);radio.checked=true;
  w.pickLane({link:0,screenshot:1,photos:2}[lane]);
  const input=w.document.getElementById({link:'linkFiles',screenshot:'gridShot',photos:'photoUpload'}[lane]);
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nAAAAABJRU5ErkJggg==','base64');
  const files=Array.from({length:count},(_,i)=>new w.File([png],'photo-'+i+'.png',{type:'image/png',lastModified:1}));
  Object.defineProperty(input,'files',{value:files,configurable:true});input.dispatchEvent(new w.Event('change',{bubbles:true}));return files;
 };
 return {w,calls,timers,set,fill,chooseFiles,close:()=>w.close()};
}
test('complete brief and delivery are saved before checkout, with no generation call',async()=>{
 const e=await environment();try{
 e.fill();e.chooseFiles('link',2);e.w.document.getElementById('addProfile').click();e.set('instagram2','https://instagram.com/friend/?utm_source=test');
 e.set('laneDetails0','Cake collapsed. Confidence did not.');e.set('details','Birthday in Tampa');e.set('names','Maya and Alex');e.set('referenceUrl','https://youtu.be/reference');e.set('artist','An artist');e.set('song','A song');e.set('genre','Pop');
 await e.w.submitInstant();
 assert.equal(e.calls.length,2);assert(e.calls[0].url.endsWith('/orders'));assert(e.calls[1].url.endsWith('/checkout'));
 const request=JSON.parse(e.calls[0].init.body.get('request'));
 assert.deepEqual(request.instagram_urls,['https://www.instagram.com/maya/','https://www.instagram.com/friend/']);assert.equal(request.captions,'Cake collapsed. Confidence did not.');assert.equal(request.names,'Maya and Alex');assert.equal(request.reference.genre,'Pop');assert.equal(request.reference.url,'https://youtu.be/reference');assert.equal(request.delivery.address,'+12025550123');assert.equal(request.delivery.method,'sms');assert.equal(request.intake,'link');assert.equal(e.calls[0].init.body.getAll('files').length,2);
 assert.equal(e.calls[1].init.headers.Authorization,'Bearer '+token);assert.equal(e.w.document.querySelector('.checkout-link').target,'');
 const stored=JSON.parse(e.w.localStorage.getItem('anthemsmith_order_v2'));assert.deepEqual(stored,saved);assert.equal(e.w.document.querySelectorAll('#builder').length,1);
 }finally{e.close();}
});
test('missing contact keeps fields editable and sends nothing',async()=>{
 const e=await environment();try{e.fill();e.chooseFiles('link',1);e.set('phone','');await e.w.submitInstant();assert.equal(e.calls.length,0);assert(e.w.document.getElementById('deliveryPhone'));assert.equal(e.w.document.getElementById('checkoutButton').disabled,false);}finally{e.close();}
});
test('screenshot uploads bytes and retains the correct caption and lane',async()=>{
 const e=await environment();try{e.fill();const files=e.chooseFiles('screenshot',1);e.set('laneDetails1','The dog owns my yoga mat.');await e.w.submitInstant();assert.equal(e.calls.length,2);const body=e.calls[0].init.body,r=JSON.parse(body.get('request'));assert.equal(r.intake,'screenshot');assert.equal(r.captions,'The dog owns my yoga mat.');assert.equal(body.getAll('files').length,1);assert.equal(body.get('files').size,files[0].size);assert.equal(r.instagram_urls.length,0);}finally{e.close();}
});
test('ten photos and email delivery survive the request boundary',async()=>{
 const e=await environment();try{e.fill();e.chooseFiles('photos',10);e.set('laneDetails2','Ten memories');const radio=e.w.document.querySelector('[name="delivery"][value="email"]');radio.checked=true;radio.dispatchEvent(new e.w.Event('change'));e.set('email','customer@example.com');await e.w.submitInstant();const body=e.calls[0].init.body,r=JSON.parse(body.get('request'));assert.equal(body.getAll('files').length,10);assert.equal(r.delivery.method,'email');assert.equal(r.delivery.address,'customer@example.com');assert.equal(r.captions,'Ten memories');assert(!JSON.stringify(r).includes('202555'));}finally{e.close();}
});
test('returning order only reads status, retains recovery, and separates queued SMS from delivered',async()=>{
 const e=await environment({stored:saved,status:'ready',url:'https://anthemsmith.com/?order='+oid});try{assert.equal(e.calls.length,1);assert.equal(e.calls[0].init.method,undefined);assert(e.w.document.querySelector('#brief audio'));assert(e.w.document.querySelector('#brief').textContent.includes('being prepared'));assert(e.w.localStorage.getItem('anthemsmith_order_v2'));}finally{e.close();}
});
test('cross-device private fragment link restores without local storage and removes token from address',async()=>{
 const e=await environment({url:'https://anthemsmith.com/?order='+oid+'#access='+token,status:'queued'});try{assert.equal(e.calls.length,1);assert.equal(e.calls[0].init.headers.Authorization,'Bearer '+token);assert.equal(e.w.location.hash,'');assert(e.w.document.querySelector('#brief a').href.includes('#access='));}finally{e.close();}
});
test('paid URL flag cannot confirm payment or start generation',async()=>{
 const e=await environment({url:'https://anthemsmith.com/?paid=1'});try{assert.equal(e.calls.length,0);assert(e.w.document.querySelector('#brief').textContent.includes('do not need to pay again'));}finally{e.close();}
});
test('network retry reuses idempotency key and preserves entered contact',async()=>{
 let fail=true;const e=await environment({handler:async(url)=>{if(url.endsWith('/orders')&&fail){fail=false;throw new Error('Network interrupted');}}});try{e.fill();e.chooseFiles('link',1);await e.w.submitInstant();assert.equal(e.w.document.getElementById('deliveryPhone').value,'(202) 555-0123');await e.w.submitInstant();const posts=e.calls.filter(c=>c.url.endsWith('/orders'));assert.equal(posts.length,2);assert.equal(posts[0].init.headers['Idempotency-Key'],posts[1].init.headers['Idempotency-Key']);}finally{e.close();}
});
test('double click cannot create duplicate requests',async()=>{
 const e=await environment();try{e.fill();e.chooseFiles('link',1);await Promise.all([e.w.submitInstant(),e.w.submitInstant()]);assert.equal(e.calls.filter(c=>c.url.endsWith('/orders')).length,1);}finally{e.close();}
});
test('needs-input recovery updates existing order and never opens another payment',async()=>{
 const e=await environment({stored:saved,status:'needs_input'});try{const field=e.w.document.querySelector('#brief textarea');field.value='We met at the dog park.';e.w.document.querySelector('#brief button').click();await tick();await tick();assert(e.calls.some(c=>c.init.method==='PATCH'));assert(!e.calls.some(c=>c.url.endsWith('/checkout')));assert(e.w.document.querySelector('#brief').textContent.includes('queue'));}finally{e.close();}
});
test('unknown status cannot falsely display ready',async()=>{
 const e=await environment({stored:saved,status:'broken'});try{assert(!e.w.document.querySelector('#brief audio'));assert(e.w.document.querySelector('#brief').textContent.includes('could not confirm'));}finally{e.close();}
});
test('paused build refuses a new order and never touches the network',async()=>{
 const e=await environment({paused:true});try{
  e.fill();
  const button=e.w.document.getElementById('checkoutButton');
  assert.equal(button.disabled,true);assert.match(button.textContent,/paused/i);
  await e.w.submitInstant();
  assert.equal(e.calls.length,0);
  assert.match(e.w.document.getElementById('requestStatus').textContent,/paused/i);
  assert.equal(e.w.document.getElementById('deliveryPhone').value,'(202) 555-0123');
 }finally{e.close();}
});
test('paused build still restores a paid order from the private link',async()=>{
 const e=await environment({paused:true,stored:saved,status:'ready'});try{
  assert.equal(e.calls.length,1);
  assert.equal(e.calls[0].init.headers.Authorization,'Bearer '+token);
  assert(e.w.document.querySelector('#brief audio'));
  assert(!e.calls.some(c=>c.url.endsWith('/orders')||c.url.endsWith('/checkout')));
 }finally{e.close();}
});
test('order and checkout URLs resolve onto the configured API mount',async()=>{
 const e=await environment({paused:false});try{
  e.fill();e.chooseFiles('link',1);await e.w.submitInstant();
  const base=new URL(e.w.ANTHEMSMITH_API_BASE);
  assert.equal(base.host,'backend-production-0f3d.up.railway.app');
  assert.equal(base.pathname,'/api');
  const [order,checkout]=e.calls.map(c=>new URL(c.url));
  assert.equal(order.href,base.href.replace(/\/$/,'')+'/orders');
  assert.equal(checkout.href,base.href.replace(/\/$/,'')+'/orders/'+oid+'/checkout');
  assert.equal(order.host,checkout.host);
 }finally{e.close();}
});

test('an Instagram link with no photo is refused before any network call',async()=>{
 const e=await environment({paused:false});try{
  e.fill();
  await e.w.submitInstant();
  assert.equal(e.calls.length,0);
  assert.match(e.w.document.getElementById('requestStatus').textContent,/photo or a grid screenshot/i);
  assert.equal(e.w.document.getElementById('instagram1').value,'https://instagram.com/maya/');
 }finally{e.close();}
});
test('photos with no Instagram link go out as a photo order, not a link order',async()=>{
 const e=await environment({paused:false});try{
  e.fill();e.set('instagram1','');
  e.chooseFiles('link',3);
  await e.w.submitInstant();
  assert.equal(e.calls.length,2);
  const body=e.calls[0].init.body,r=JSON.parse(body.get('request'));
  assert.equal(r.intake,'photos');
  assert.equal(r.instagram_urls.length,0);
  assert.equal(body.getAll('files').length,3);
 }finally{e.close();}
});
test('the Instagram lane shows a required upload before the optional link',async()=>{
 const e=await environment({paused:false});try{
  const lane0=e.w.document.getElementById('lane0'),upload=lane0.querySelector('#linkFiles');
  assert(upload,'lane0 must carry its own upload');
  assert.equal(upload.type,'file');
  assert.equal(upload.multiple,true);
  assert.match(lane0.textContent,/Photos or a grid screenshot/);
  assert.match(lane0.textContent,/Required/);
  assert.match(lane0.textContent,/optional context/);
  assert.equal(e.w.document.getElementById('instagram1').required,false);
  assert(lane0.textContent.indexOf('Photos or a grid screenshot')<lane0.textContent.indexOf('Instagram URL'),'upload reads before the link');
 }finally{e.close();}
});

const normalId='a0b9b371-c86e-4934-b625-f37bfec8ae81';
const normalToken='N'.repeat(43);
const normalOrder={order_id:normalId,token:normalToken};
const player='https://anthemsmith.com/backend-song.html';
const reply=body=>new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});

test('production UUID draft persists its order-scoped tab credential before checkout and recovers on the return',async()=>{
 const e=await environment({handler:(url,init)=>url.endsWith('/orders') && init.method==='POST'
   ? reply({order_id:normalId,recovery_token:normalToken,status:'draft'}) : null});
 let tabOrder;
 try {
  e.fill();e.chooseFiles('photos',1);await e.w.submitInstant();
  assert.equal(e.calls.length,2);
  assert.match(e.calls[1].url,new RegExp('/orders/'+normalId+'/checkout$'));
  assert.equal(e.calls[1].init.headers.Authorization,'Bearer '+normalToken);
  tabOrder=JSON.parse(e.w.sessionStorage.getItem('anthemsmith_private_'+normalId));
  assert.deepEqual(tabOrder,normalOrder);
  assert.equal(e.w.localStorage.getItem('anthemsmith_order_v2'),null);
  assert.equal(e.w.document.querySelector('#brief a').href,player+'#order='+normalId+'&token='+normalToken);
 } finally {e.close();}
 const back=await environment({url:player+'?order='+normalId,tabOrder,paused:true,
   handler:()=>reply({order_id:normalId,status:'paid'})});
 try {
  assert.equal(back.calls.length,1);
  assert.match(back.calls[0].url,new RegExp('/orders/'+normalId+'$'));
  assert.equal(back.calls[0].init.headers.Authorization,'Bearer '+normalToken);
  assert.equal(back.calls[0].init.method,undefined);
  assert.match(back.w.document.querySelector('#brief').textContent,/Payment confirmed/);
 } finally {back.close();}
});

for(const status of ['awaiting_payment','draft'])test('paused cancelled '+status+' return can recheck without opening checkout',async()=>{
 const e=await environment({url:player+'?order='+normalId+'&cancelled=1',tabOrder:normalOrder,paused:true,
   handler:()=>reply({order_id:normalId,status})});
 try {
  const brief=e.w.document.getElementById('brief');
  assert.match(brief.textContent,/Checkout was closed/);
  assert.match(brief.textContent,/Payment has not been confirmed/);
  assert.match(brief.textContent,/Checkout is paused/);
  const actions=[...brief.querySelectorAll('button')];
  assert.deepEqual(actions.map(x=>x.textContent),['Check payment status']);
  actions[0].click();await tick();
  assert.equal(e.calls.length,2);
  for(const c of e.calls)assert.equal(c.init.method,undefined);
  assert.equal(brief.querySelector('.checkout-link'),null);
 } finally {e.close();}
});

test('unpaused cancelled return reopens only the explicitly requested saved checkout',async()=>{
 const e=await environment({url:player+'?order='+normalId+'&cancelled=1',tabOrder:normalOrder,
   handler:url=>url.endsWith('/checkout')?null:reply({order_id:normalId,status:'awaiting_payment'})});
 try {
  assert.equal(e.calls.length,1);
  const resume=[...e.w.document.querySelectorAll('#brief button')].find(b=>b.textContent==='Continue to payment');
  resume.click();await tick();
  assert.equal(e.calls.length,2);
  assert.match(e.calls[1].url,new RegExp('/orders/'+normalId+'/checkout$'));
  assert.equal(e.calls[1].init.method,'POST');
  assert.equal(e.calls[1].init.headers['Idempotency-Key'],normalId+'-checkout');
 } finally {e.close();}
});

test('normal notification fragment restores and scrubs its address before status, even on a fresh browser',async()=>{
 const e=await environment({url:player+'#order='+normalId+'&token='+normalToken,paused:true,
   handler:()=>reply({order_id:normalId,status:'queued'})});
 try {
  assert.equal(e.w.location.href,player+'?order='+normalId);
  assert.equal(e.calls.length,1);
  assert.equal(e.calls[0].init.headers.Authorization,'Bearer '+normalToken);
  assert(!e.calls[0].url.includes(normalToken));
  assert.equal(e.calls[0].init.redirect,'error');
  assert.match(e.w.document.querySelector('#brief').textContent,/song is in the queue/);
 } finally {e.close();}
});

for(const [description,suffix,tabOrder] of [
 ['fresh browser','?order='+normalId,null],
 ['different saved order','?order='+normalId,{order_id:'f515ff3d-16f8-4cfe-bbc9-4a7dd5c85c1e',token:normalToken}],
 ['short fragment','?order='+normalId+'#access=short',normalOrder],
 ['query credential','?order='+normalId+'&token='+normalToken,normalOrder],
 ['duplicate fragment order','#order='+normalId+'&order='+normalId+'&token='+normalToken,normalOrder],
 ['conflicting identities','?order='+normalId+'#order='+normalId+'&token='+normalToken,normalOrder],
 ['duplicate query order','?order='+normalId+'&order='+normalId,normalOrder],
])test(description+' shows recovery guidance without an order request',async()=>{
 const e=await environment({url:player+suffix,tabOrder,paused:true,stored:saved});
 try {
  assert.equal(e.calls.length,0);
  assert.equal(e.w.location.hash,'');
  assert(!e.w.location.href.includes(normalToken));
  assert.match(e.w.document.getElementById('brief').textContent,/complete private order link/);
 } finally {e.close();}
});

test('ready UUID uses its authenticated download, then enables play and download after full-length audio can play',async()=>{
 const e=await environment({url:player+'?order='+normalId,tabOrder:normalOrder,paused:true,
   handler:url=>url.endsWith('/download')
    ? new Response(new Uint8Array([1,2,3,4]),{headers:{'Content-Type':'audio/mpeg'}})
    : reply({order_id:normalId,status:'ready',audio_url:'https://example.invalid/stale',notification_receipt:{status:'accepted'}})});
 try {
  await tick();
  const brief=e.w.document.getElementById('brief'), audio=brief.querySelector('audio'), link=brief.querySelector('[download]');
  assert.equal(e.calls.length,2);
  assert.match(e.calls[1].url,new RegExp('/orders/'+normalId+'/download$'));
  assert.equal(e.calls[1].init.headers.Authorization,'Bearer '+normalToken);
  assert.equal(e.calls[1].init.redirect,'error');
  assert.equal(audio.getAttribute('src'),'blob:fixture');
  assert.equal(link.hidden,true);
  Object.defineProperty(audio,'duration',{value:204});
  audio.dispatchEvent(new e.w.Event('canplay'));
  assert.equal(audio.hidden,false);assert.equal(link.hidden,false);
  assert.equal(link.getAttribute('href'),'blob:fixture');
  assert.match(brief.textContent,/Delivery confirmation is pending/);
  e.w.dispatchEvent(new e.w.Event('pagehide'));
  assert.equal(link.hasAttribute('href'),false);
  assert.equal(e.calls[1].init.signal.aborted,true);
 } finally {e.close();}
});

for(const [description,response] of [
 ['non-audio',()=>new Response('<html>',{headers:{'Content-Type':'text/html'}})],
 ['oversized audio',()=>new Response('X',{headers:{'Content-Type':'audio/mpeg','Content-Length':String(21*1024*1024)}})],
 ['denied download',()=>new Response('',{status:404})],
])test(description+' offers a retry without a playable private file',async()=>{
 const e=await environment({url:player+'?order='+normalId,tabOrder:normalOrder,paused:true,
   handler:url=>url.endsWith('/download')?response():reply({order_id:normalId,status:'ready'})});
 try {
  await tick();
  const brief=e.w.document.getElementById('brief');
  assert.equal(brief.querySelector('[download]').hasAttribute('href'),false);
  assert.match(brief.textContent,/Retry loading song/);
  assert.equal(e.calls.length,2);
 } finally {e.close();}
});

test('an unverified fragment cannot replace a previously verified tab credential for the same order',async()=>{
 const wrong='Z'.repeat(43);
 const e=await environment({url:player+'#order='+normalId+'&token='+wrong,tabOrder:normalOrder,
   handler:()=>new Response(JSON.stringify({error:'not_found'}),{status:404})});
 try {
  assert.equal(e.w.location.href,player+'?order='+normalId);
  assert.equal(e.calls.length,1);
  assert.deepEqual(JSON.parse(e.w.sessionStorage.getItem('anthemsmith_private_'+normalId)),normalOrder);
 } finally {e.close();}
});

for(const status of ['running','ingesting','measuring_reference','vibe_reading','spec','qc'])test('working stage '+status+' shows progress and polls the same order',async()=>{
 const e=await environment({url:player+'?order='+normalId,tabOrder:normalOrder,paused:true,
   handler:()=>reply({order_id:normalId,status})});
 try {
  const text=e.w.document.getElementById('brief').textContent;
  assert.doesNotMatch(text,/could not confirm|check again|continue to payment/i);
  const refresh=[...e.timers.values()].find(timer=>timer.delay===5000);
  assert.ok(refresh);
  await refresh.fn();
  assert.equal(e.calls.length,2);
  assert(e.calls.every(c=>c.url.endsWith('/orders/'+normalId)&&c.init.method===undefined));
 } finally {e.close();}
});

for(const status of ['blocked_spec','blocked_profile','needs_reference','needs_sources','needs_vision','notification_uncertain'])test('held stage '+status+' offers support without checkout or automatic polling',async()=>{
 const e=await environment({url:player+'?order='+normalId,tabOrder:normalOrder,
   handler:()=>reply({order_id:normalId,status})});
 try {
  const brief=e.w.document.getElementById('brief');
  assert.match(brief.textContent,/this order ID/);assert.match(brief.textContent,/do not need to pay again/);
  assert(brief.querySelector('a[href="https://www.instagram.com/anthemsmith/"]'));
  assert.equal(brief.querySelector('button,.checkout-link'),null);
  assert.equal(e.calls.length,1);assert.equal(e.timers.size,0);
 } finally {e.close();}
});
