/* Durable orders: private intake -> verified payment -> recoverable delivery.
 * The server is authoritative for payment, generation and notification status.
 * No public relay, client-paid flag, or browser-triggered generation exists here.
 */
(function () {
  'use strict';
  const form = document.getElementById('songForm');
  const brief = document.getElementById('brief');
  const feedback = document.getElementById('requestStatus');
  const button = document.getElementById('checkoutButton');
  const storageKey = 'anthemsmith_order_v2';
  const api = new URL(window.ANTHEMSMITH_API_BASE || '/api', location.origin);
  const laneFields = {link:'laneDetails0', screenshot:'laneDetails1', photos:'laneDetails2'};
  let pending = null, busy = false, pollTimer = null, networkFailures = 0;

  if (api.protocol !== 'https:' && !['localhost','127.0.0.1'].includes(api.hostname)) {
    feedback.textContent = 'Checkout is temporarily unavailable. Please try again shortly.';
    button.disabled = true;
    return;
  }
  const endpoint = path => api.href.replace(/\/$/, '') + path;
  function safeUrl(value, allowedHosts) {
    const url = new URL(value, location.origin);
    if (url.protocol !== 'https:' && !['localhost','127.0.0.1'].includes(url.hostname)) throw new Error('Invalid link.');
    if (allowedHosts && !allowedHosts.includes(url.hostname)) throw new Error('Invalid checkout link.');
    return url.href;
  }
  function phoneNumber(value) {
    const raw = value.trim();
    if (!/^[+\d\s().-]+$/.test(raw)) throw new Error('Enter a valid mobile number, or choose email.');
    const digits = raw.replace(/\D/g, '');
    const normalized = raw.startsWith('+') ? '+' + digits : digits.length === 10 ? '+1' + digits : digits.length === 11 && digits[0] === '1' ? '+' + digits : '';
    if (!/^\+[1-9]\d{7,14}$/.test(normalized)) throw new Error('Include your country code, starting with +, or choose email.');
    return normalized;
  }
  function instagram(value) {
    if (!value.trim()) return null;
    const u = new URL(value.trim());
    const match = u.pathname.match(/^\/([A-Za-z0-9_.]{1,30})\/?$/);
    if (u.protocol !== 'https:' || !['instagram.com','www.instagram.com'].includes(u.hostname) || !match || ['p','reel','reels','stories','explore','accounts'].includes(match[1].toLowerCase())) throw new Error('Paste a complete Instagram profile link.');
    return 'https://www.instagram.com/' + match[1] + '/';
  }
  function collect() {
    const values = Object.fromEntries(Array.from(new FormData(form)).filter(([,v]) => typeof v === 'string'));
    const lane = values.intake_lane;
    if (!(lane in laneFields)) throw new Error('Choose how you want to share your story.');
    const files = selectedIntakeFiles();
    if (lane === 'link' && !values.instagram1?.trim()) throw new Error('Add your Instagram profile link.');
    if (lane !== 'link' && !files.length) throw new Error('Choose your photos or a grid screenshot.');
    const max = lane === 'screenshot' ? 2 : 10;
    if (files.length > max || files.reduce((s,f)=>s+f.size,0) > 50*1024*1024 || files.some(f=>f.size<=0 || f.size>15*1024*1024 || !['image/jpeg','image/png','image/webp'].includes(f.type))) throw new Error('Use JPG, PNG or WebP images: up to 15 MB each and 50 MB total.');
    if (values.consent !== 'yes') throw new Error('Please confirm you have permission to share this content.');
    const delivery = values.delivery;
    if (!['sms','email'].includes(delivery)) throw new Error('Choose text or email delivery.');
    const contact = delivery === 'sms' ? phoneNumber(values.phone || '') : (values.email || '').trim();
    if (delivery === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact)) throw new Error('Enter a valid email address, or choose text.');
    const profiles = lane === 'link' ? [instagram(values.instagram1 || ''), instagram(values.instagram2 || '')].filter(Boolean) : [];
    const referenceUrl = (values.referenceUrl || '').trim();
    if (referenceUrl) safeUrl(referenceUrl,['youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','youtu.be','open.spotify.com','spotify.link']);
    const request = {
      schema_version:2,
      intake:lane,
      instagram_urls:profiles,
      captions:(values[laneFields[lane]] || '').trim(),
      details:(values.details || '').trim(),
      names:(values.names || '').trim(),
      category:values.category,
      mood:values.mood,
      voice:values.voice,
      reference:{url:referenceUrl,artist:(values.artist||'').trim(),song:(values.song||'').trim(),genre:values.genre||''},
      delivery:{method:delivery,address:contact},
      consent:true,
      files:files.map(f=>({name:f.name,type:f.type,size:f.size,last_modified:f.lastModified}))
    };
    return {request,files};
  }
  function setDelivery() {
    const sms = form.querySelector('[name="delivery"]:checked').value === 'sms';
    document.getElementById('phoneField').hidden = !sms;
    document.getElementById('emailField').hidden = sms;
    const phone = document.getElementById('deliveryPhone'), email = document.getElementById('deliveryEmail');
    phone.disabled = !sms; phone.required = sms;
    email.disabled = sms; email.required = !sms;
  }
  form.querySelectorAll('[name="delivery"]').forEach(el=>el.addEventListener('change',setDelivery));
  setDelivery();
  form.addEventListener('input',()=>{feedback.textContent='';});

  async function jsonFetch(path, options={}) {
    const controller = new AbortController();
    const timer = setTimeout(()=>controller.abort(),90000);
    try {
      const r = await fetch(endpoint(path),{...options,signal:controller.signal,credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
      const body = await r.json().catch(()=>null);
      if (!r.ok || !body || typeof body !== 'object') throw new Error(body?.message || (r.status===429?'Please wait a moment, then try again.':'We could not reach your order. Please try again.'));
      return body;
    } finally {clearTimeout(timer);}
  }
  function persist(order) {
    // Store only the private recovery credential, never the photos or contact.
    try {localStorage.setItem(storageKey,JSON.stringify(order));} catch (_) { /* recovery link remains visible */ }
  }
  function recoveryUrl(order) {
    const url = new URL(location.pathname,location.origin);
    url.searchParams.set('order',order.order_id);
    url.hash = new URLSearchParams({access:order.recovery_token}).toString();
    return url.href;
  }
  function validOrder(order) {
    return order && /^AS-[A-Za-z0-9_-]{8,80}$/.test(order.order_id) && /^[A-Za-z0-9_-]{32,256}$/.test(order.recovery_token);
  }
  function clearPoll() {if(pollTimer) clearTimeout(pollTimer);pollTimer=null;}
  function showOrder(order, heading, message) {
    brief.hidden=false;brief.replaceChildren();
    const h=document.createElement('h2');h.textContent=heading;
    const p=document.createElement('p');p.textContent=message;
    const id=document.createElement('p');id.className='small';id.textContent='Order '+order.order_id;
    const recover=document.createElement('a');recover.href=recoveryUrl(order);recover.textContent='Save this private order link';recover.className='text-button';recover.referrerPolicy='no-referrer';
    brief.append(h,p,id,recover);
    return brief;
  }
  function action(text, handler) {
    const b=document.createElement('button');b.type='button';b.className='primary';b.textContent=text;b.addEventListener('click',handler);brief.append(document.createElement('br'),b);return b;
  }
  function showCheckout(order, checkoutUrl) {
    showOrder(order,'Your request is saved.','Complete secure payment to start your song. Keep your private order link so you can return from any device.');
    const link=document.createElement('a');link.href=safeUrl(checkoutUrl,['checkout.stripe.com','buy.stripe.com']);link.textContent='Pay securely — $5';link.className='primary checkout-link';
    // Same-tab checkout avoids relying on sessionStorage shared across windows.
    brief.append(document.createElement('br'),link);
    action('Check payment status',()=>watch(order));
  }
  async function createCheckout(order) {
    const data=await jsonFetch('/orders/'+encodeURIComponent(order.order_id)+'/checkout',{method:'POST',headers:{Authorization:'Bearer '+order.recovery_token,'Idempotency-Key':order.order_id+'-checkout'}});
    showCheckout(order,data.checkout_url);
  }
  async function watch(order) {
    clearPoll();
    try {
      const data=await jsonFetch('/orders/'+encodeURIComponent(order.order_id),{headers:{Authorization:'Bearer '+order.recovery_token}});
      networkFailures=0;
      if(data.order_id!==order.order_id) throw new Error('Order lookup mismatch. Please try again.');
      if(data.status==='awaiting_payment' || data.status==='draft') {
        showOrder(order,'Your request is saved.','Payment has not been confirmed yet. You can safely return to checkout.');
        action('Continue to payment',async()=>{try{await createCheckout(order);}catch(e){showOrder(order,'Your request is saved.',e.message);action('Try again',()=>watch(order));}});
        action('Check payment status',()=>watch(order));return;
      }
      if(data.status==='ready') {
        showOrder(order,'Your song is ready.','Play it here, or save the full song.');
        if(data.audio_url) {
          const url=safeUrl(data.audio_url);const audio=document.createElement('audio');audio.controls=true;audio.preload='none';audio.src=url;
          const link=document.createElement('a');link.href=url;link.textContent='Download your song';link.className='text-button';link.download='AnthemSmith-song.mp3';link.referrerPolicy='no-referrer';brief.append(audio,link);
        } else {const p=document.createElement('p');p.textContent='Your download link is being prepared. Check again in a moment.';brief.append(p);action('Refresh download',()=>watch(order));}
        const note=document.createElement('p');note.className='small';note.textContent=data.notification_status==='delivered'?'Your delivery message was delivered.':data.notification_status==='failed'?'We could not deliver the message. Your song is safe here.':'Your delivery message is being prepared.';brief.append(note);return;
      }
      if(data.status==='needs_input') {
        showOrder(order,'Your song needs one more detail.',data.message || 'Add a caption or an inside joke so the song can tell your story.');
        const label=document.createElement('label');label.textContent='Add a caption, memory or inside joke';const field=document.createElement('textarea');field.maxLength=12000;label.append(field);brief.append(label);
        const error=document.createElement('p');error.setAttribute('role','status');brief.append(error);
        const b=action('Save details',async()=>{
          if(!field.value.trim()){error.textContent='Add a little detail before continuing.';field.focus();return;}
          b.disabled=true;
          try{await jsonFetch('/orders/'+encodeURIComponent(order.order_id)+'/details',{method:'PATCH',headers:{Authorization:'Bearer '+order.recovery_token,'Content-Type':'application/json'},body:JSON.stringify({details:field.value.trim()})});await watch(order);}catch(e){error.textContent=e.message;b.disabled=false;}
        });return;
      }
      if(['failed','cancelled','refunded'].includes(data.status)) {
        showOrder(order,data.status==='refunded'?'Your payment was refunded.':'Your song needs attention.',data.message || 'Contact us with this order ID. You do not need to pay again.');
        const support=document.createElement('a');support.href='https://www.instagram.com/anthemsmith/';support.textContent='Contact @AnthemSmith';support.target='_blank';support.rel='noopener noreferrer';brief.append(document.createElement('br'),support);return;
      }
      const labels={paid:['Payment confirmed.','Your order is saved. We will send your song when it is ready.'],queued:['Your song is in the queue.','You can close this page. Use your private link to check back.'],processing:['Reading your story.','We are finding the details that make the song yours.'],generating:['Making your song.','You can close this page. We will send a link when it is ready.']};
      if(!labels[data.status])throw new Error('We could not confirm the latest order status. Your private link will still work.');
      showOrder(order,...labels[data.status]);
      pollTimer=setTimeout(()=>watch(order),5000);
    }catch(e){
      networkFailures++;
      showOrder(order,'Your order link is saved.',e.name==='AbortError'?'The connection timed out. Check again in a moment.':e.message);
      action('Check again',()=>watch(order));
      if(networkFailures<4)pollTimer=setTimeout(()=>watch(order),Math.min(30000,5000*networkFailures));
    }
  }

  window.submitInstant=async function() {
    if(busy || !form.reportValidity())return;
    busy=true;button.disabled=true;feedback.textContent='Saving your story…';
    try{
      const {request,files}=collect();
      // A content fingerprint preserves idempotency on a network retry, including
      // files with identical names but different bytes. Changed input gets a new key.
      const hashes=[];
      for(const f of files){const bytes=await crypto.subtle.digest('SHA-256',await f.arrayBuffer());hashes.push(Array.from(new Uint8Array(bytes),n=>n.toString(16).padStart(2,'0')).join(''));}
      const fingerprint=JSON.stringify([request,hashes]);
      if(!pending || pending.fingerprint!==fingerprint)pending={fingerprint,key:crypto.randomUUID()};
      const payload=new FormData();payload.append('request',JSON.stringify(request));files.forEach(f=>payload.append('files',f,f.name));
      const data=await jsonFetch('/orders',{method:'POST',headers:{'Idempotency-Key':pending.key},body:payload});
      const order={order_id:data.order_id,recovery_token:data.recovery_token};
      if(!validOrder(order))throw new Error('We could not save your recovery link. Please try again before paying.');
      persist(order);feedback.textContent='';
      showOrder(order,'Your request is saved.','Preparing your secure payment link. Keep your private order link so you can return later.');
      if(data.status==='awaiting_payment' || data.status==='draft') await createCheckout(order);
      else await watch(order);
      brief.scrollIntoView({behavior:'smooth',block:'start'});
    }catch(e){feedback.textContent=e.name==='AbortError'?'Saving timed out. Try again; the same request will not be duplicated.':e.message;}
    finally{busy=false;button.disabled=false;}
  };
  function restore() {
    const search=new URLSearchParams(location.search),hash=new URLSearchParams(location.hash.slice(1));
    let order=null;
    const linked={order_id:search.get('order'),recovery_token:hash.get('access')};
    if(validOrder(linked)){order=linked;persist(order);history.replaceState(null,'',location.pathname+'?order='+encodeURIComponent(order.order_id));}
    else {try{const saved=JSON.parse(localStorage.getItem(storageKey));if(validOrder(saved)&&(!search.get('order')||search.get('order')===saved.order_id))order=saved;}catch(_){}}
    if(order)watch(order);
    else if(search.has('order') || search.get('paid')==='1') {
      brief.hidden=false;brief.textContent='Use the private order link from your confirmation to check your song. If you paid through the earlier checkout, message @AnthemSmith so we can recover it. You do not need to pay again.';
    }
  }
  restore();
  window.addEventListener('pagehide',clearPoll);
})();
