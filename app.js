const stories=[{name:'My Story',tag:'A LIFE WORTH SINGING',title:'Every chapter counts.',desc:'Your roots, your comeback, your next great chapter.',image:0,moods:['Life journey','Hometown roots','Comeback','Celebration','Surprise me'],caption:'Sample caption: “First studio. Same dream I had at sixteen.”',lyric:'Sixteen-year-old dreams in a room of my own, / Every color on these walls says I made it home.'},{name:'Funny',tag:'A LITTLE LOVE. A LITTLE ROAST.',title:'The legend lives on.',desc:'That one friend. That one story. That unforgettable chorus.',image:1,moods:['Playful roast','Inside jokes','Birthday chaos','Surprise me'],caption:'Sample caption: “The cake collapsed. Our confidence did not.”',lyric:'You called it a masterpiece, we called the floor, / Best worst birthday we keep coming back for.'},{name:'Couples',tag:'YOUR VERY OWN LOVE SONG',title:'Our kind of forever.',desc:'From the first hello to the kitchen slow dance.',image:2,moods:['In love','Crush','Missing you','Breakup','Anniversary','Surprise me'],caption:'Sample caption: “No reservations. Just our kitchen and this song.”',lyric:'Two washes, extra noodles, one sock gone astray, / I’d choose you in this laundromat on any given day.'},{name:'Friendship',tag:'FOR YOUR FAVORITE PEOPLE',title:'Wrong turn. Right people.',desc:'The road trips, the laughter, the partners in crime.',image:3,moods:['Best friends','Partners in crime','Funny','Flirty','Falling out','Surprise me'],caption:'Sample caption: “Missed the exit. Found our favorite day.”',lyric:'We lost the map but found our way, / You’re the people I would choose on any day.'}];
const rawStories=[
{title:'Still here. Still building.',tag:'MY STORY / THE UNEDITED VERSION',desc:'The late shifts, second chances, and things you built yourself.',caption:'Fictional caption: “45. First shop with my name on the door.”',comment:'Fictional comment: “Still wearing the boots from your first job.”',lyric:'Same old boots on a brand-new floor, / Forty-five years brought me to this door.'},
{title:'He said he’d help.',tag:'FUNNY / A REGULAR SATURDAY',desc:'Ten minutes into gardening. He’s supervising.',caption:'Fictional caption: “He said he’d help.”',comment:'Fictional comment: “Hasn’t moved a single plant.”',lyric:'You came to move the garden, took the wheelbarrow seat, / Now I’m doing all the digging while you put up your feet.'},
{title:'Spin cycle soulmates.',tag:'COUPLES / LOVE, WITH EXTRA NOODLES',desc:'No grand gestures. Just you, me, and dryer number three.',caption:'Fictional caption: “Date night. Two washes and extra noodles.”',comment:'Fictional comment: “You still owe me the last dumpling.”',lyric:'Two washes, extra noodles, one sock gone astray, / I’d choose you in this laundromat on any given day.'},
{title:'He knows the regulars.',tag:'FRIENDSHIP / EVERYONE’S FAVORITE',desc:'We came to see our friends. They came to see the dog.',caption:'Fictional caption: “Pretty sure we’re just his ride home.”',comment:'Fictional comment: “He gets greeted before we do.”',lyric:'They shout your name before they ask for mine, / Green bandana, pink tongue, stealing all the limelight.'}
];
const AS_EMAIL='gabrieljtao@gmail.com';

// Incident AS-MUIJ11B6: stop new purchases while uploads and delivery are repaired.
// This is containment, not a substitute for server-side payment and source gates.
const AS_ORDERING_PAUSED=true;
function showOrderingPaused(){
 const box=$('#brief');if(!box)return;
 box.hidden=false;
 box.innerHTML='<h2>New orders are temporarily paused.</h2><p>We are repairing uploads and song delivery. Please do not send a payment.</p><p>Already paid? Your order still matters. Contact <a href="https://www.instagram.com/anthemsmith/" target="_blank" rel="noopener">@AnthemSmith</a> or email <a href="mailto:'+AS_EMAIL+'">'+AS_EMAIL+'</a> with your order ID. Do not pay again.</p>';
}

stories.forEach((s,i)=>Object.assign(s,rawStories[i]));
// Published demo selections. Keep reference masters unchanged.
const samples=[{genre:'Boom-bap Hip-hop',voice:'Female',src:'assets/audio/mystory-hook-30.mp3'},{genre:'Pop-punk',voice:'Male',src:'assets/audio/funny-hook-30.mp3'},{genre:'Art-pop / dream synth',voice:'Female',src:'assets/audio/couples-hook-30.mp3'},{genre:'Hip-hop',voice:'Male',src:'assets/audio/friendship-hook-30.mp3'}];
function sample(i){const x=samples[i];return `<div class="song-sample"><span class="sample-label">${x.genre} · ${x.voice} vocals</span><p class="sample-lyric">${stories[i].lyric.replace(' / ','<br>')}</p>${x.src?`<audio controls preload="none" aria-label="30-second ${stories[i].name} song sample" src="${x.src}"></audio>`:`<button class="sample-pending" disabled aria-label="30-second sample not recorded yet">▶ &nbsp; 30-second sample · Coming soon</button>`}</div>`}
let current=0,activeStory=1;
const $=s=>document.querySelector(s);
const builder=$('#builder');
function photo(i){return `<div role="img" aria-label="${['Fictional woman in her workshop','Fictional casually dressed adult couple joking with a wheelbarrow in their backyard','Fictional couple sharing noodles at a laundromat','Altered illustrative photo of two men and a black-and-white dog at a bar'][i]}" class="photo q${i}"></div>`}
const mayaPosts=[
{title:'New apartment',likes:'1,284',caption:'Moved out. Took <mark>the plant Mum said I’d kill</mark>.'},
{title:'First 5K',likes:'2,106',caption:'Ran a 5K. <mark>Complained for 4.9 of it.</mark>'},
{title:'Baking era',likes:'938',caption:'Cake collapsed. <mark>Confidence remains unreasonable.</mark>'},
{title:'Friday dinner',likes:'1,762',caption:'Dressed up to <mark>split one plate of fries</mark>.'},
{title:'Morning routine',likes:'1,403',caption:'Bought a yoga mat. <mark>The dog owns it now.</mark>'},
{title:'Twenty-five',likes:'2,341',caption:'25. <mark>Still calling Mum about laundry.</mark>'}
];
const mayaDemo={title:'Unreasonably Confident',genre:'Dark whisper-pop',voice:'Female',src:'assets/audio/maya-full.mp3',lyric:'Five K done, complained through most, / Burnt the cake but here’s my toast: / Twenty-five, still figuring it out, / Got a dog on my yoga mat and a life to laugh about.'};
function mayaPlayer(){return `<div class="maya-player"><span class="eyebrow">YOUR POSTS → YOUR CHORUS · HEAR THE FULL SONG</span><h3>${mayaDemo.title}</h3><p class="small">${mayaDemo.genre} · ${mayaDemo.voice} vocals · Full biography song · 3:29</p><blockquote>${mayaDemo.lyric.replaceAll(' / ','<br>')}</blockquote>${mayaDemo.src?`<audio controls preload="none" src="${mayaDemo.src}" aria-label="Maya’s full biography song"></audio>`:'<button class="sample-pending" disabled>▶ &nbsp; 30-second song demo · Recording pending</button>'}</div>`}
function onePlayerLaw(){document.addEventListener('play',e=>{if(e.target.tagName==='AUDIO')document.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause()})},true)};function render(){document.body.dataset.design='0';
$('#site').innerHTML=`<nav><a class="brand" href="#" aria-label="AnthemSmith home"><span class="brand-icon"><img src="assets/anthemsmith-mark.webp" alt=""></span><span class="brand-name">🎶 AnthemSmith🎵🔨</span></a><div class="nav-links"><a href="#stories">The stories</a><a href="#how">How it works</a><button class="nav-button" data-create="0">Make a song</button></div></nav>
<section class="hero narrative-hero"><div class="copy"><span class="eyebrow">ANTHEMSMITH · YOUR VIBE, FORGED INTO A SONG</span><h1>A picture is worth<br>a thousand words.<br><em>Your Instagram<br>is worth a song.</em></h1><p class="intro">The nights worth remembering. The people you’ll never forget. A personal song made from the stories between your posts.</p><button class="primary" data-create="0">Ordering paused</button><a class="explore" href="#stories">Find your kind of story ↓</a><p class="small">One original song. 3–4 minutes. All you.</p></div><section class="maya-profile" aria-label="Fictional Instagram biography example"><header><div><b>Maya, 25</b><span>@maya.aftercoffee · Fictional profile</span></div><span class="demo-label">DEMO</span></header><p class="profile-bio">new city · old friends · questionable baking</p><p class="profile-disclosure">Illustrative posts and like counts — not customer reviews.</p><div class="maya-grid">${mayaPosts.map((p,i)=>`<article class="maya-post"><div class="maya-photo m${i}" role="img" aria-label="Fictional Maya: ${p.title}"></div><div class="maya-caption"><span class="likes">♡ ${p.likes} likes</span><p>${p.caption}</p></div></article>`).join('')}</div>${mayaPlayer()}</section></section>
<section class="story-intro" id="stories"><span class="eyebrow">EVERYONE HAS ONE.</span><h2>What’s your anthem?</h2><p>A love letter. A little roast. A whole life in a chorus.</p></section>
<section class="story-grid" aria-label="Four kinds of songs">${stories.map((s,i)=>`<article class="story-card"><button class="photo-button" data-demo="${i}" aria-label="Explore ${s.name}">${photo(i)}</button><div class="story-body"><span class="eyebrow">0${i+1} / ${s.name}</span><h3>${s.title}</h3><p>${s.desc}</p>${sample(i)}<button class="text-button story-cta" data-create="${i}">${['Tell my story','Roast my friend','Make our love song','Make our anthem'][i]} ↗</button></div></article>`).join('')}</section><p class="example-note">Fictional stories and sample lyrics — hear the real demos in each player.</p>
<section class="how-story" id="how"><div class="how-copy"><span class="eyebrow">MORE THAN A SINGLE PHOTO</span><h2>We find the story<br>between the posts.</h2><p>You show us the moments — the photos you pick, the captions you paste, the links you share. What was really going on, who was there, the joke nobody else gets.</p><p>Hand us the pieces you want included. Together, they become a song that feels like you.</p></div><div class="lyric-recipe"><span class="eyebrow">FROM A POST TO A CHORUS · FICTIONAL EXAMPLE</span><div class="recipe-line"><span>THE CAPTION</span><p>“45. First shop with my name on the door.”</p></div><div class="recipe-line"><span>THE COMMENT</span><p>“Still wearing the boots from your first job.”</p></div><div class="recipe-song"><span>THE SONG</span><blockquote>Same old boots on a brand-new floor,<br>Forty-five years brought me to this door.</blockquote><small>Boom-bap Hip-hop · Female vocals</small></div></div></section>
<div id="soundSlot"></div>
<section hidden class="input-ways" id="how-it-works"><span class="eyebrow">HOW IT WORKS</span><h2>Three ways in.<br>Any of them works.</h2><p class="lede">Pick whichever is easiest for you. You hand us the moments — we never go and take them. No passwords, no access to your account, nothing you have to unlock.</p><div class="ways"><article class="way"><span class="num">1</span><div class="art"><svg viewBox="0 0 260 150" width="100%" height="auto" role="img" aria-label="A public Instagram link being pasted"><rect x="8" y="26" width="180" height="34" rx="4" fill="#fff" stroke="#c6c5be" stroke-width="1.4"/><circle cx="28" cy="43" r="7" fill="none" stroke="#9d9c94" stroke-width="1.6"/><text x="44" y="48" font-family="DM Sans,sans-serif" font-size="13" fill="#66665e">instagram.com/…</text><path d="M196 43 h22" stroke="#d93127" stroke-width="2.6" stroke-linecap="round"/><path d="M212 37 l7 6 -7 6" fill="none" stroke="#d93127" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/><g fill="#e6e3db" stroke="#c6c5be" stroke-width="1.2"><rect x="8" y="86" width="30" height="30" rx="2"/><rect x="42" y="86" width="30" height="30" rx="2"/><rect x="76" y="86" width="30" height="30" rx="2"/><rect x="110" y="86" width="30" height="30" rx="2"/></g><g fill="#9d9c94"><circle cx="23" cy="101" r="5"/><circle cx="57" cy="99" r="5"/><circle cx="91" cy="103" r="5"/><circle cx="125" cy="100" r="5"/></g><rect x="152" y="86" width="92" height="30" rx="3" fill="none" stroke="#d93127" stroke-width="1.6" stroke-dasharray="4 4"/><text x="198" y="106" font-family="DM Sans,sans-serif" font-size="11" fill="#d93127" text-anchor="middle">public only</text></svg></div><h3>Drop your link</h3><p>Share your public profile. Paste any captions or inside jokes you want us to include, especially if a post needs context.</p><span class="best">PUBLIC PROFILES</span></article><article class="way"><span class="num">2</span><div class="art"><svg viewBox="0 0 260 150" width="100%" height="auto" role="img" aria-label="A phone showing a screenshot of an Instagram grid"><rect x="96" y="8" width="76" height="134" rx="10" fill="#fff" stroke="#20211e" stroke-width="2"/><rect x="104" y="20" width="60" height="106" rx="3" fill="#f4f2ed" stroke="#c6c5be" stroke-width="1"/><g fill="#dcd9d1"><rect x="106" y="22" width="18" height="18"/><rect x="125" y="22" width="18" height="18"/><rect x="144" y="22" width="18" height="18"/><rect x="106" y="41" width="18" height="18"/><rect x="125" y="41" width="18" height="18"/><rect x="144" y="41" width="18" height="18"/><rect x="106" y="60" width="18" height="18"/><rect x="125" y="60" width="18" height="18"/><rect x="144" y="60" width="18" height="18"/></g><rect x="130" y="14" width="12" height="3" rx="1.5" fill="#20211e"/><g stroke="#d93127" stroke-width="2.4" fill="none" stroke-linecap="round"><path d="M62 20 h-10 v12"/><path d="M206 20 h10 v12"/><path d="M62 130 h-10 v-12"/><path d="M206 130 h10 v-12"/></g><text x="40" y="80" font-family="DM Sans,sans-serif" font-size="11" fill="#66665e" text-anchor="middle" transform="rotate(-90 40 80)">screenshot</text></svg></div><h3>Screenshot your grid</h3><p>Already have your feed open? Screenshot the grid and send it. Fastest option if you are on your phone right now.</p><span class="best">FASTEST</span></article><article class="way"><span class="num">3</span><div class="art"><svg viewBox="0 0 260 150" width="100%" height="auto" role="img" aria-label="Ten photo tiles being uploaded"><g fill="none" stroke="#c6c5be" stroke-width="1.4"><g><rect x="8" y="10" width="44" height="44" rx="3"/><rect x="58" y="10" width="44" height="44" rx="3"/><rect x="108" y="10" width="44" height="44" rx="3"/><rect x="158" y="10" width="44" height="44" rx="3"/><rect x="208" y="10" width="44" height="44" rx="3"/><rect x="8" y="60" width="44" height="44" rx="3"/><rect x="58" y="60" width="44" height="44" rx="3"/><rect x="108" y="60" width="44" height="44" rx="3"/><rect x="158" y="60" width="44" height="44" rx="3"/><rect x="208" y="60" width="44" height="44" rx="3"/></g></g><g fill="#e6e3db"><rect x="9" y="11" width="42" height="42" rx="3"/><rect x="59" y="11" width="42" height="42" rx="3"/><rect x="109" y="11" width="42" height="42" rx="3"/><rect x="59" y="61" width="42" height="42" rx="3"/><rect x="159" y="61" width="42" height="42" rx="3"/></g><g fill="#9d9c94"><circle cx="30" cy="30" r="7"/><circle cx="80" cy="26" r="7"/><circle cx="130" cy="32" r="7"/><circle cx="80" cy="82" r="7"/><circle cx="180" cy="78" r="7"/></g><g stroke="#d93127" stroke-width="2.6" fill="none" stroke-linecap="round"><path d="M230 34 v22 M230 34 l-8 9 M230 34 l8 9"/></g><text x="230" y="76" font-family="Space Grotesk,sans-serif" font-size="12" font-weight="700" fill="#d93127" text-anchor="middle">10</text></svg></div><h3>Show us the moments</h3><p>Upload up to ten photos — the ones that actually mean something. Straight from your camera roll, no account needed.</p><span class="best">WORKS FOR EVERYONE</span></article></div><div class="recover"><div class="field"><b>Paste the captions, or just tell us about these</b>The bit only you know — who is in it, what was actually going on, the joke nobody else gets…</div><div class="why"><strong>Optional, but it is the good stuff.</strong> Photos give us the pictures; this gives us the words. It is where the chorus comes from — and it needs no account and no link.</div></div><p class="ways-note">Nothing here asks for your password or access to your account. If your Instagram is private, that is completely fine — use photos or a screenshot instead.</p></section><section class="private-story"><div><span class="eyebrow">PRIVATE ACCOUNT? YOU’RE INVITED.</span><h2>Let us into your world.</h2><p>Use photos or a grid screenshot, then add your favorite captions and inside jokes. You can also message @AnthemSmith for help.</p></div><a class="instagram-button" href="https://www.instagram.com/anthemsmith/" target="_blank" rel="noopener">Find @AnthemSmith ↗</a></section>
<section class="closing"><span class="eyebrow">FOR YOU. FOR THEM. FOR THE GROUP CHAT.</span><h2>The soundtrack to your story.</h2><button class="primary" data-create="0">Ordering paused</button></section><footer><a class="brand" href="#"><span class="brand-icon"><img src="assets/anthemsmith-mark.webp" alt=""></span><span class="brand-name">🎶 AnthemSmith🎵🔨</span></a><a class="footer-ig" href="https://www.instagram.com/anthemsmith/" target="_blank" rel="noopener">instagram.com/anthemsmith</a><span>New orders are temporarily paused. Existing customers: contact @AnthemSmith with your order ID; do not pay again. <a href="terms.html">Terms</a> · <a href="privacy.html">Privacy</a></span></footer>`;$('#soundSlot').replaceWith(builder)}
function moods(){const s=stories.find(s=>s.name===$('#categorySelect').value);$('#moodSelect').innerHTML=s.moods.map(x=>`<option>${x}</option>`).join('')}
function openBuilder(i){$('#demo').close();$('#songForm').hidden=true;showOrderingPaused();builder.scrollIntoView({behavior:'smooth',block:'start'})}
document.addEventListener('click',e=>{let b=e.target.closest('[data-create]');if(b){openBuilder(+b.dataset.create);return}b=e.target.closest('[data-demo]');if(b){activeStory=+b.dataset.demo;const s=stories[activeStory];$('#demoTitle').textContent=s.title;$('#demoPhoto').className='photo q'+s.image;$('#demoCaption').textContent=s.caption;$('#demoSample').innerHTML=sample(+b.dataset.demo);$('#demo').showModal()}if(e.target.closest('.close'))e.target.closest('dialog').close()});
$('#demoCreate').onclick=()=>openBuilder(activeStory);$('#categorySelect').onchange=moods;
$('#referenceUrl').addEventListener('input',e=>{const field=e.target;field.setCustomValidity('');if(!field.value.trim())return;try{const u=new URL(field.value);const host=u.hostname.toLowerCase();if(u.protocol!=='https:'||!['youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','youtu.be','open.spotify.com','spotify.link'].includes(host))field.setCustomValidity('Paste an HTTPS YouTube or Spotify song link.')}catch{field.setCustomValidity('Paste a complete YouTube or Spotify link.')}});
render();onePlayerLaw();moods();
$('#songForm').hidden=true;
showOrderingPaused();
const pauseBanner=document.createElement('p');pauseBanner.setAttribute('role','status');pauseBanner.className='small';pauseBanner.textContent='New orders are temporarily paused while we repair uploads and delivery. Already paid? Contact @AnthemSmith with your order ID. Do not pay again.';$('#site').prepend(pauseBanner);

$('#addProfile').onclick=()=>{const open=$('#secondProfile').hidden;$('#secondProfile').hidden=!open;$('#instagram2').disabled=!open;$('#addProfile').textContent=open?'− Remove second Instagram':'+ Add a second Instagram';if(open)$('#instagram2').focus()};
for(const selector of ['#instagram1','#instagram2']){$(selector).addEventListener('input',e=>{const f=e.target;f.setCustomValidity('');if(!f.value.trim())return;try{const u=new URL(f.value);if(u.protocol!=='https:'||!['instagram.com','www.instagram.com'].includes(u.hostname.toLowerCase())||u.pathname==='/'||/^\/(p|reel|reels|stories)\//.test(u.pathname))f.setCustomValidity('Paste an Instagram profile URL, not a post link.')}catch{f.setCustomValidity('Paste a complete Instagram profile URL.')}})}

// ===== DELIVERY VALIDATION — exactly one valid contact, enforced at submit =====
function normalizePhone(raw){
 raw=String(raw||'').trim();
 let d=raw.replace(/[^\d]/g,'');
 if(!d)return '';
 if(!raw.includes('+')&&d.length<=10)d='1'+d;      // bare US/CA number -> +1 country code
 if(d.length<11||d.length>15)return '';             // E.164 width: cc(1-3)+subscriber
 if(/^(0{2,}|1)$/.test(d))return '';                // no all-zero / lone country code
 return '+'+d;
}
function normalizeEmail(raw){
 const v=String(raw||'').trim().toLowerCase();
 if(!v)return '';
 const at=v.indexOf('@');
 if(at<1)return '';                                 // no @, or empty local part
 const dom=v.slice(at+1);
 if(dom.includes('@'))return '';                    // single @ only
 if(!/^[a-z0-9.-]+\.[a-z]{2,}$/.test(dom))return ''; // domain needs a real TLD dot
 return v;
}
function validateContact(mode,raw){
 return mode==='sms'?{ok:!!normalizePhone(raw),value:normalizePhone(raw)}:{ok:!!normalizeEmail(raw),value:normalizeEmail(raw)};
}
const intakeFiles={screenshot:[],photos:[]};
const fileConfig={screenshot:{input:'gridShot',status:'shotName',preview:'shotPreviews',limit:2},photos:{input:'photoUpload',status:'photoCount',preview:'photoPreviews',limit:10}};
const fileUrls={screenshot:[],photos:[]};
function activeLane(){return document.querySelector('[name="intake_lane"]:checked').value}
function pickLane(n){
 for(let i=0;i<3;i++){
  const section=document.getElementById('lane'+i);section.hidden=i!==n;
  section.querySelectorAll('input,textarea,button').forEach(el=>el.disabled=i!==n);
 }
 $('#instagram1').required=n===0;
 $('#instagram2').disabled=n!==0||$('#secondProfile').hidden;
 for(const kind of ['screenshot','photos']){const el=document.getElementById(fileConfig[kind].input);el.required=false;el.setCustomValidity('')}
}
function showFiles(kind){
 const c=fileConfig[kind],container=document.getElementById(c.preview);
 fileUrls[kind].forEach(URL.revokeObjectURL);fileUrls[kind]=[];container.replaceChildren();
 intakeFiles[kind].forEach((file,index)=>{
  const figure=document.createElement('figure');figure.className='file-preview';
  const img=document.createElement('img');const url=URL.createObjectURL(file);fileUrls[kind].push(url);img.src=url;img.alt='Selected photo '+(index+1);
  const cap=document.createElement('figcaption');cap.textContent=file.name;
  const remove=document.createElement('button');remove.type='button';remove.textContent='Remove';remove.setAttribute('aria-label','Remove '+file.name);
  remove.onclick=()=>{intakeFiles[kind].splice(index,1);document.getElementById(c.input).value='';document.getElementById(c.input).setCustomValidity('');showFiles(kind)};
  figure.append(img,cap,remove);container.append(figure);
 });
 document.getElementById(c.status).textContent=intakeFiles[kind].length+' selected';
}
for(const kind of ['screenshot','photos']){
 const c=fileConfig[kind],input=document.getElementById(c.input);
 input.addEventListener('change',()=>{
  const selected=Array.from(input.files);let error='';
  if(selected.length>c.limit)error='Choose at most '+c.limit+' '+(kind==='photos'?'photos.':'screenshot'+(c.limit>1?'s':'')+'.');
  else if(selected.some(f=>!['image/jpeg','image/png','image/webp'].includes(f.type)))error='Use JPG, PNG or WebP images.';
  else if(selected.some(f=>f.size===0||f.size>15*1024*1024))error='Each image must contain data and be no larger than 15 MB.';
  else if(selected.reduce((total,f)=>total+f.size,0)>50*1024*1024)error='Keep the selected images under 50 MB in total.';
  if(error){input.value='';input.setCustomValidity(error);document.getElementById(c.status).textContent=error;return}
  input.setCustomValidity('');if(selected.length)intakeFiles[kind]=selected;showFiles(kind);
 });
}
function selectedIntakeFiles(){return intakeFiles[activeLane()]||[]}
function requestData(){
 const values=Object.fromEntries(Array.from(new FormData($('#songForm'))).filter(([,v])=>typeof v==='string'));
 values.schema_version=1;values.request_id='AS-'+Date.now().toString(36).toUpperCase();values.created_at=new Date().toISOString();
 values.status='prepared_not_sent';values.currency='USD';values.quoted_price=5;
 values.files=selectedIntakeFiles().map((f,i)=>({path:'photos/'+String(i+1).padStart(2,'0')+'-'+f.name.replace(/[^a-zA-Z0-9._-]/g,'_'),original_name:f.name,type:f.type,size:f.size}));
 return values;
}
function requestText(data){return JSON.stringify(data,null,1)}
// ===== INSTANT FLOW: IG link -> Stripe $5 -> repo_dispatch -> forge -> play =====
const GH_OWNER='LittleDoorClub',GH_REPO='anthemsmith-site-live';
function orderStatusURL(id){return 'https://raw.githubusercontent.com/'+GH_OWNER+'/'+GH_REPO+'/main/songs/'+id+'.json'}
function blockedStatusURL(id){return 'https://raw.githubusercontent.com/'+GH_OWNER+'/'+GH_REPO+'/main/songs/'+id+'.blocked.json'}
function failedStatusURL(id){return 'https://raw.githubusercontent.com/'+GH_OWNER+'/'+GH_REPO+'/main/songs/'+id+'.failed.json'}
function songURL(id){return 'https://raw.githubusercontent.com/'+GH_OWNER+'/'+GH_REPO+'/main/songs/'+id+'.mp3'}
async function fireOrder(data){
 throw new Error('New orders are temporarily paused. Existing customers: do not pay again.');
}
function watchOrder(id,box,tries){
 if(typeof id!=='string'||!/^AS-[A-Z0-9-]{1,64}$/.test(id)){showOrderingPaused();return}
 box.innerHTML='<p class="small">Checking order '+id+'. Do not pay again.</p>';
 let n=0;
 const timer=setInterval(async()=>{
  n++;
  try{
   const [r,rb,rf]=await Promise.all([
    fetch(orderStatusURL(id)+'?t='+Date.now()).catch(()=>null),
    fetch(blockedStatusURL(id)+'?t='+Date.now()).catch(()=>null),
    fetch(failedStatusURL(id)+'?t='+Date.now()).catch(()=>null)
   ]);
   const manifest=r&&r.ok?await r.json().catch(()=>null):null;
   // An old failure marker must not hide audio that is already ready.
   if(manifest&&['audio_ready','delivered'].includes(manifest.status)){
    clearInterval(timer);
    const full=songURL(id);
    box.innerHTML='<h3>Your song is ready.</h3><p>Your song is available here. You can play or download it below.</p><audio controls src="'+full+'"></audio><p><a href="'+full+'" download>Download the full song</a></p>';
    return;
   }
   if((rb&&rb.ok)||(rf&&rf.ok)){
    clearInterval(timer);
    box.innerHTML='<h3>We need to recover your order.</h3><p>We could not finish order '+id+'. Please contact <a href="https://www.instagram.com/anthemsmith/" target="_blank" rel="noopener">@AnthemSmith</a> or email <a href="mailto:'+AS_EMAIL+'">'+AS_EMAIL+'</a> with this order ID. <strong>Do not pay again.</strong></p>';
    return;
   }
   if(n>(tries||90)){
    clearInterval(timer);
    box.innerHTML='<p>Order '+id+' is taking longer than expected. Contact @AnthemSmith or <a href="mailto:'+AS_EMAIL+'">'+AS_EMAIL+'</a> with your order ID. Do not pay again.</p>';
   }
  }catch{
   if(n>(tries||90)){clearInterval(timer);showOrderingPaused()}
  }
 },4000);
}
function payChoicePanel(values){showOrderingPaused();}
async function submitInstant(){showOrderingPaused();}
function resumeAfterPayment(){
 // Viewing an existing order is read-only. A return URL does not prove payment.
 let data={};
 try{data=JSON.parse(sessionStorage.getItem('anthemsmith_order')||'{}')||{}}catch{}
 const id=data.request_id||new URLSearchParams(location.search).get('oid');
 if(typeof id!=='string'||!/^AS-[A-Z0-9-]{1,64}$/.test(id)){showOrderingPaused();return false}
 const box=$('#brief');box.hidden=false;
 watchOrder(id,box);
 return true;
}
window.addEventListener('load',()=>{
 const resume=()=>resumeAfterPayment();
 if(new URLSearchParams(location.search).get('paid')==='1'){resume();}
 else if(sessionStorage.getItem('anthemsmith_order')){
  const box=$('#brief');if(!box)return;box.hidden=false;
  box.innerHTML='<p class="small">Welcome back. You can check your existing order — <a href="#" id="resumeOrder">view my order</a>.</p>';
  const a=document.getElementById('resumeOrder');
  if(a)a.onclick=(e)=>{e.preventDefault();resume();};
 }
});

pickLane(0);

window.__renderDelivery=function(){
 const m=(document.querySelector('input[name="delivery"]:checked')||{}).value||'sms';
 const df=document.getElementById('deliveryFields');
 if(df)df.innerHTML = m==='sms'
  ? '<input type="tel" id="deliveryPhone" name="sms_to" autocomplete="tel" placeholder="Your cell number — song lands by SMS" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px">'
  : '<input type="email" id="deliveryEmail" name="email" autocomplete="email" placeholder="you@email.com — song lands in your inbox" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px">';
};
