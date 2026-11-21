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
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const oldOrder = /^AS-[A-Za-z0-9_-]{8,80}$/;
  const cancelledReturn = new URLSearchParams(location.search).get('cancelled') === '1';
  const api = new URL(window.ANTHEMSMITH_API_BASE || '/api', location.origin);
  const laneFields = {link:'laneDetails0', screenshot:'laneDetails1', photos:'laneDetails2'};
  let pending = null, busy = false, pollTimer = null, networkFailures = 0;
  let watchVersion = 0, clearMedia = () => {};

  if (api.protocol !== 'https:' && !['localhost','127.0.0.1'].includes(api.hostname)) {
    (feedback || brief).textContent = 'The order service is temporarily unavailable. Please try again shortly.';
    if (button) button.disabled = true;
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
  function trackUrl(value) {
    const raw=value.trim();
    if (!raw) return '';
    let url;
    try { url=new URL(raw); } catch (_) {}
    const message='Paste a YouTube video link or a Spotify track link from open.spotify.com.';
    if (!url || url.protocol!=='https:' || url.username || url.password || url.port) throw new Error(message);
    if (url.hostname==='open.spotify.com') {
      const track=url.pathname.match(/^\/(?:intl-[a-z]{2}\/)?track\/([a-zA-Z0-9]{22})\/?$/);
      if (track) return 'https://open.spotify.com/track/'+track[1];
    }
    const youtube=['www.youtube.com','youtube.com','m.youtube.com','music.youtube.com'];
    let video;
    if (url.hostname==='youtu.be') video=url.pathname.replace(/^\/|\/$/g,'');
    else if (youtube.includes(url.hostname)) {
      if (url.pathname==='/watch') video=url.searchParams.get('v');
      else video=url.pathname.match(/^\/(?:embed|shorts)\/([a-zA-Z0-9_-]{11})\/?$/)?.[1];
    }
    if (video && /^[a-zA-Z0-9_-]{11}$/.test(video)) return 'https://www.youtube.com/watch?v='+video;
    throw new Error(message);
  }
  function updateSoundFields() {
    const fields=form.elements;
    const artist=fields.artist.value.trim(), song=fields.song.value.trim(), url=fields.referenceUrl;
    fields.genre.required=!url.value.trim() && !artist && !song;
    fields.artist.required=!url.value.trim() && Boolean(song);
    fields.song.required=!url.value.trim() && Boolean(artist);
    try {trackUrl(url.value);url.setCustomValidity('');}
    catch (error) {url.setCustomValidity(error.message);}
  }
  function collect() {
    const values = Object.fromEntries(Array.from(new FormData(form)).filter(([,v]) => typeof v === 'string'));
    const lane = values.intake_lane;
    if (!(lane in laneFields)) throw new Error('Choose how you want to share your story.');
    const files = selectedIntakeFiles();
    // The upload is the anchor on EVERY lane: production rejects an Instagram order with no
    // photo (422 instagram_photo_upload_required). The profile link is optional context.
    if (!files.length) throw new Error('Add at least one photo or a grid screenshot — that is what your song is built from.');
    const max = lane === 'screenshot' ? 2 : 10;
    if (files.length > max || files.reduce((s,f)=>s+f.size,0) > 50*1024*1024 || files.some(f=>f.size<=0 || f.size>15*1024*1024 || !['image/jpeg','image/png','image/webp'].includes(f.type))) throw new Error('Use JPG, PNG or WebP images: up to 15 MB each and 50 MB total.');
    if (values.consent !== 'yes') throw new Error('Please confirm you have permission to share this content.');
    const delivery = values.delivery;
    if (!['sms','email'].includes(delivery)) throw new Error('Choose text or email delivery.');
    const contact = delivery === 'sms' ? phoneNumber(values.phone || '') : (values.email || '').trim();
    if (delivery === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact)) throw new Error('Enter a valid email address, or choose text.');
    const profiles = lane === 'link' ? [instagram(values.instagram1 || ''), instagram(values.instagram2 || '')].filter(Boolean) : [];
    const reference={url:trackUrl(values.referenceUrl || ''), artist:(values.artist || '').trim(),
      song:(values.song || '').trim(), genre:values.genre || ''};
    if (!['Male','Female'].includes(values.voice)) throw new Error('Choose a voice for your song.');
    if (!reference.url && Boolean(reference.artist)!==Boolean(reference.song)) {
      throw new Error('Enter both the artist and song title, or clear them and choose a genre.');
    }
    if (!reference.genre && !reference.url && !reference.artist) {
      throw new Error('Choose a genre, paste a song link, or enter both an artist and song title.');
    }
    // A link-lane order with no usable link is a photo order; never claim 'link' intake with
    // an empty instagram_urls, because production validates the link lane as link-plus-photo.
    const intake = lane === 'link' && !profiles.length ? 'photos' : lane;
    const request = {
      schema_version:2,
      intake,
      instagram_urls:profiles,
      captions:(values[laneFields[lane]] || '').trim(),
      details:(values.details || '').trim(),
      names:(values.names || '').trim(),
      category:values.category,
      mood:values.mood,
      voice:values.voice,
      reference,
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

  // Containment gate (incident). Ordering stays paused until live verification passes, so no
  // new order, upload or payment can leave this page. Recovery of an existing order is NOT paused:
  // restore()/watch() below still read the private link and show a paid customer their song.
  const PAUSED = window.ANTHEMSMITH_ORDERING_PAUSED !== false;
  const PAUSE_MESSAGE = 'New orders are temporarily paused while we verify uploads and payment. '
    + 'Already paid? Open your saved private order link. Do not pay again.';
  if (form) {
    document.querySelectorAll('[data-pause-notice]').forEach(el=>{el.hidden=!PAUSED;});
    if (PAUSED) document.querySelectorAll('[data-paused-label]').forEach(el=>{el.textContent=el.dataset.pausedLabel;});
    form.querySelectorAll('[name="delivery"]').forEach(el=>el.addEventListener('change',setDelivery));
    setDelivery();
    updateSoundFields();
    form.addEventListener('input',event=>{
      feedback.textContent='';
      if (['genre','artist','song','referenceUrl'].includes(event.target.name)) updateSoundFields();
    });
    form.addEventListener('invalid',event=>{
      const details=event.target.closest('details');
      if (details) details.open=true;
    },true);
    button.disabled = PAUSED;
    button.textContent = PAUSED ? 'Ordering paused' : 'Continue to secure payment — $5';
    if (PAUSED) feedback.textContent = PAUSE_MESSAGE;
  }

  async function jsonFetch(path, options={}) {
    const controller = new AbortController();
    const timer = setTimeout(()=>controller.abort(),90000);
    try {
      const r = await fetch(endpoint(path),{...options,signal:controller.signal,credentials:'omit',cache:'no-store',redirect:'error',referrerPolicy:'no-referrer'});
      const body = await r.json().catch(()=>null);
      if (!r.ok || !body || typeof body !== 'object') throw new Error(body?.message || (r.status===429?'Please wait a moment, then try again.':'We could not reach your order. Please try again.'));
      return body;
    } finally {clearTimeout(timer);}
  }
  function persist(order) {
    // Store only the private recovery credential, never the photos or contact.
    try {
      if (uuid.test(order.order_id)) {
        sessionStorage.setItem('anthemsmith_private_'+order.order_id,JSON.stringify({order_id:order.order_id,token:order.recovery_token}));
      } else {
        localStorage.setItem(storageKey,JSON.stringify(order));
      }
    } catch (_) { /* The customer can still save the complete private link. */ }
  }
  function recoveryUrl(order) {
    if (uuid.test(order.order_id)) {
      const url = new URL('backend-song.html',location.href);
      url.hash = new URLSearchParams({order:order.order_id,token:order.recovery_token}).toString();
      return url.href;
    }
    const url = new URL(location.pathname,location.origin);
    url.searchParams.set('order',order.order_id);
    url.hash = new URLSearchParams({access:order.recovery_token}).toString();
    return url.href;
  }
  function validOrder(order) {
    if (!order || typeof order.order_id !== 'string' || typeof order.recovery_token !== 'string') return false;
    return uuid.test(order.order_id) ? /^[A-Za-z0-9_-]{43}$/.test(order.recovery_token)
      : oldOrder.test(order.order_id) && /^[A-Za-z0-9_-]{32,256}$/.test(order.recovery_token);
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
    if (PAUSED) throw new Error('Checkout is paused. You can check your saved order here.');
    const data=await jsonFetch('/orders/'+encodeURIComponent(order.order_id)+'/checkout',{method:'POST',headers:{Authorization:'Bearer '+order.recovery_token,'Idempotency-Key':order.order_id+'-checkout'}});
    showCheckout(order,data.checkout_url);
  }
  async function loadPrivateSong(order) {
    const controller=new AbortController();
    const audio=document.createElement('audio'), download=document.createElement('a'), message=document.createElement('p');
    const limit=20*1024*1024;
    let objectUrl, timeout;
    audio.controls=true; audio.preload='auto'; audio.hidden=true;
    download.textContent='Download your song'; download.className='text-button'; download.download='AnthemSmith-song.mp3'; download.hidden=true;
    message.setAttribute('role','status'); message.textContent='Loading your full song…';
    brief.append(message,audio,download);
    clearMedia=() => {
      controller.abort();
      clearTimeout(timeout);
      audio.removeAttribute('src');
      download.removeAttribute('href');
      audio.hidden=true; download.hidden=true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl=null;
    };
    function failed() {
      if (controller.signal.aborted) return;
      clearMedia();
      message.textContent='Your full song could not be loaded. Retry this order or contact @AnthemSmith. Do not pay again.';
      action('Retry loading song',()=>watch(order));
    }
    try {
      timeout=setTimeout(failed,90000);
      const response=await fetch(endpoint('/orders/'+encodeURIComponent(order.order_id)+'/download'),{
        headers:{Authorization:'Bearer '+order.recovery_token},credentials:'omit',cache:'no-store',
        referrerPolicy:'no-referrer',redirect:'error',signal:controller.signal
      });
      if (controller.signal.aborted) return;
      if (response.status!==200 || !/^audio\/(mpeg|mp3)(;|$)/i.test(response.headers.get('Content-Type') || '') ||
          Number(response.headers.get('Content-Length'))>limit || !response.body) throw new Error();
      const reader=response.body.getReader(), parts=[];
      let size=0;
      try {
        for (;;) {
          const {done,value}=await reader.read();
          if (done) break;
          size+=value.byteLength;
          if (size>limit) throw new Error();
          parts.push(value);
        }
      } finally {reader.releaseLock();}
      if (controller.signal.aborted) return;
      if (!size) throw new Error();
      objectUrl=URL.createObjectURL(new Blob(parts,{type:'audio/mpeg'}));
      function fullLength() {
        return Number.isFinite(audio.duration) && audio.duration>=180 && audio.duration<=240;
      }
      audio.addEventListener('loadedmetadata',()=>{if (!fullLength()) failed();});
      audio.addEventListener('error',failed);
      audio.addEventListener('canplay',()=>{
        if (controller.signal.aborted) return;
        if (!fullLength()) {failed();return;}
        clearTimeout(timeout);
        message.textContent='Your full song is ready to play or download.';
        audio.hidden=false;download.href=objectUrl;download.hidden=false;
      });
      message.textContent='Checking your full song…';
      audio.src=objectUrl;
      audio.load();
    } catch (_) {failed();}
  }
  async function watch(order) {
    const version=++watchVersion;
    clearPoll();
    clearMedia();
    try {
      const data=await jsonFetch('/orders/'+encodeURIComponent(order.order_id),{headers:{Authorization:'Bearer '+order.recovery_token}});
      if (version!==watchVersion) return;
      networkFailures=0;
      if(data.order_id!==order.order_id || typeof data.status!=='string') throw new Error('Order lookup mismatch. Please try again.');
      persist(order);
      if(data.status==='awaiting_payment' || data.status==='draft') {
        const heading = cancelledReturn ? 'Checkout was closed. Your request is saved.' : 'Your request is saved.';
        const message = PAUSED ? 'Payment has not been confirmed. Checkout is paused. You can check this order here.'
          : 'Payment has not been confirmed. Check its status before resuming checkout.';
        showOrder(order,heading,message);
        if (!PAUSED) action('Continue to payment',async()=>{try{await createCheckout(order);}catch(e){showOrder(order,'Your request is saved.',e.message);action('Try again',()=>watch(order));}});
        action('Check payment status',()=>watch(order));return;
      }
      if(data.status==='ready' || data.status==='delivered') {
        showOrder(order,'Your song is ready.','Play it here, or save the full song.');
        if (uuid.test(order.order_id)) {
          loadPrivateSong(order);
        } else if(data.audio_url) {
          const url=safeUrl(data.audio_url);const audio=document.createElement('audio');audio.controls=true;audio.preload='none';audio.src=url;
          const link=document.createElement('a');link.href=url;link.textContent='Download your song';link.className='text-button';link.download='AnthemSmith-song.mp3';link.referrerPolicy='no-referrer';brief.append(audio,link);
        } else {const p=document.createElement('p');p.textContent='Your download link is being prepared. Check again in a moment.';brief.append(p);action('Refresh download',()=>watch(order));}
        const note=document.createElement('p');note.className='small';
        const receipt=data.notification_receipt?.status || data.notification_status;
        if(receipt==='delivered') note.textContent='Your delivery message was delivered.';
        else if(['accepted','sent','already_sent','submitted'].includes(receipt)) note.textContent='Your delivery message was submitted. Delivery confirmation is pending.';
        else if(['failed','blocked','uncertain'].includes(receipt)) note.textContent='Delivery of your message could not be confirmed. Your song is available here.';
        else note.textContent='Your delivery message is being prepared.';
        brief.append(note);return;
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
      const held={
        blocked_spec:'We need to review the song plan before continuing.',
        blocked_profile:'We need to review the details used to build your song.',
        needs_reference:'We need help matching your reference track. Include the artist and song title when you contact us.',
        needs_sources:'We need more of your story. Contact us to confirm which photos or captions to add.',
        needs_vision:'We could not finish reviewing your photos.',
        notification_uncertain:'We are checking whether your delivery message reached you.'
      };
      if(Object.hasOwn(held,data.status) || ['failed','payment_failed','cancelled','canceled','cancel','refunded'].includes(data.status)) {
        const instructions='Contact @AnthemSmith with this order ID. You do not need to pay again.';
        const message=Object.hasOwn(held,data.status) ? held[data.status] : (data.message || '');
        showOrder(order,data.status==='refunded'?'Your payment was refunded.':'Your order needs attention.',[message,instructions].filter(Boolean).join(' '));
        const support=document.createElement('a');support.href='https://www.instagram.com/anthemsmith/';support.textContent='Contact @AnthemSmith';support.target='_blank';support.rel='noopener noreferrer';brief.append(document.createElement('br'),support);return;
      }
      const labels={
        paid:['Payment confirmed.','Your order is saved. We will send your song when it is ready.'],
        queued:['Your song is in the queue.','You can close this page. Use your private link to check back.'],
        running:['Your song is in progress.','Use your private link to check back.'],
        processing:['Reading your story.','We are finding the details that make the song yours.'],
        ingesting:['Reading your story.','We are reviewing the material you shared.'],
        measuring_reference:['Checking your reference track.','We are working out the musical details for your song.'],
        vibe_reading:['Reading your story.','We are working on the style of your song.'],
        spec:['Planning your song.','We are bringing together your story and musical preferences.'],
        generating:['Making your song.','You can close this page. We will send a link when it is ready.'],
        qc:['Checking your song.','We are checking the finished recording.']
      };
      if(!Object.hasOwn(labels,data.status))throw new Error('We could not confirm the latest order status. Your private link will still work.');
      showOrder(order,...labels[data.status]);
      pollTimer=setTimeout(()=>watch(order),5000);
    }catch(e){
      if (version!==watchVersion) return;
      networkFailures++;
      showOrder(order,'Your order link is saved.',e.name==='AbortError'?'The connection timed out. Check again in a moment.':e.message);
      action('Check again',()=>watch(order));
      if(networkFailures<4)pollTimer=setTimeout(()=>watch(order),Math.min(30000,5000*networkFailures));
    }
  }

  if (form) window.submitInstant=async function() {
    if(PAUSED){feedback.textContent=PAUSE_MESSAGE;button.disabled=true;return;}
    if(busy)return;
    updateSoundFields();
    if(!form.reportValidity())return;
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
    if (window.restoreLegacyOrder?.(search, hash, brief, () => {
      try { return validOrder(JSON.parse(localStorage.getItem(storageKey))); }
      catch { return false; }
    })) return;
    let order=null;
    const id=search.get('order');
    const querySecret=search.has('token') || search.has('access');
    const addressed=search.has('order') || search.has('paid') || search.has('cancelled') || querySecret;
    const hasFragment=Boolean(location.hash);
    if (!querySecret && search.getAll('order').length<=1) {
      if (hasFragment) {
        if (!id && hash.size===2 && hash.has('order') && hash.has('token') && uuid.test(hash.get('order'))) {
          order={order_id:hash.get('order'),recovery_token:hash.get('token')};
        } else if (id && hash.size===1 && hash.has('access')) {
          order={order_id:id,recovery_token:hash.get('access')};
        }
        if (!validOrder(order)) order=null;
      } else if (uuid.test(id || '')) {
        try {
          const saved=JSON.parse(sessionStorage.getItem('anthemsmith_private_'+id));
          if (saved?.order_id===id) order={order_id:id,recovery_token:saved.token};
        } catch (_) {}
      } else if (oldOrder.test(id || '') || (!addressed && form)) {
        try {
          const saved=JSON.parse(localStorage.getItem(storageKey));
          if (oldOrder.test(saved?.order_id || '') && (!id || saved.order_id===id)) order=saved;
        } catch (_) {}
      }
    }
    // Scrub even invalid capabilities. An ID-only return can only use its own saved credential.
    if (querySecret || (hasFragment && (!form || addressed || hash.has('order') || hash.has('token') || hash.has('access')))) {
      const cleanId=validOrder(order) ? order.order_id : id;
      const clean=new URLSearchParams();
      if (uuid.test(cleanId || '') || oldOrder.test(cleanId || '')) clean.set('order',cleanId);
      if (cancelledReturn && clean.has('order')) clean.set('cancelled','1');
      history.replaceState(null,'',location.pathname+(clean.size?'?'+clean:''));
    }
    if (validOrder(order)) watch(order);
    else if (!form || addressed || hash.has('order') || hash.has('token') || hash.has('access')) {
      brief.hidden=false;
      brief.textContent='An order ID alone cannot open your song. Open the complete private order link you saved or received, or return to the original checkout tab. You do not need to pay again. ';
      const support=document.createElement('a');
      support.href='mailto:cozyafllc@gmail.com';
      support.textContent='Email AnthemSmith with your order ID for help.';
      brief.append(support);
    }
  }
  restore();
  window.addEventListener('pagehide',()=>{watchVersion++;clearPoll();clearMedia();});
  window.addEventListener('pageshow',event=>{if(event.persisted) restore();});
})();
