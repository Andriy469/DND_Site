const $ = id => document.getElementById(id);
let auth = null, state = null, ws = null, currentView = "overview", selectedToken = null, toastTimer = null;

const viewMeta = {
  overview:["CAMPAIGN DASHBOARD","Огляд пригоди"],
  map:["TACTICAL BATTLEFIELD","Ігрова карта"],
  characters:["HEROES & CREATURES","Персонажі та NPC"],
  combat:["ENCOUNTER TRACKER","Бойова система"],
  dice:["FATE & CHANCE","Кубики"],
  history:["CAMPAIGN CHRONICLE","Історія подій"]
};
function toast(message){const el=$("toast");el.textContent=message;el.classList.add("show");clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove("show"),2800)}
function escapeHtml(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function setError(id,msg){$(id).textContent=msg||""}
async function api(path, opts={}) {
  const headers = {...(opts.headers||{})};
  if(auth?.access_key) headers["X-Access-Key"]=auth.access_key;
  if(opts.body && !(opts.body instanceof FormData)) headers["Content-Type"]="application/json";
  const res=await fetch(path,{...opts,headers});
  const body=await res.json().catch(()=>({detail:"Невідома помилка сервера"}));
  if(!res.ok) throw new Error(body.detail||"Помилка запиту");
  return body;
}
function showGate(){ $("gate").classList.remove("hidden"); $("game").classList.add("hidden"); if(ws){ws.close();ws=null} auth=null;state=null }
function showGame(){
  $("gate").classList.add("hidden");$("game").classList.remove("hidden");
  $("campaign-title").textContent=state?.session?.name||"Пригода";
  $("copy-code").textContent=auth.code;
  $("role-chip").textContent=auth.role==="GM"?"✦ GAME MASTER":"✦ PLAYER";
  $("user-name").textContent=auth.name;
  $("user-role").textContent=auth.role;
  $("user-avatar").textContent=(auth.name||"?").slice(0,1).toUpperCase();
  document.querySelectorAll(".gm-only").forEach(el=>el.classList.toggle("hidden",auth.role!=="GM"));
  $("gm-map-tools").classList.toggle("hidden",auth.role!=="GM");
  setView(currentView);
}
async function loadState(){
  if(!auth)return;
  try{state=await api(`/api/sessions/${auth.session_id}/state`);renderAll();if(!$("game").classList.contains("hidden"))showGame();}
  catch(e){toast(e.message)}
}
function connectWs(){
  if(ws)ws.close();
  const scheme=location.protocol==="https:"?"wss":"ws";
  ws=new WebSocket(`${scheme}://${location.host}/ws/${auth.session_id}?key=${encodeURIComponent(auth.access_key)}`);
  ws.onopen=()=>{$("connection").classList.add("connected");$("connection").innerHTML='<span class="dot"></span> Синхронізовано';};
  ws.onmessage=ev=>{try{const msg=JSON.parse(ev.data);if(msg.state){state=msg.state;renderAll()}}catch(e){}};
  ws.onclose=()=>{$("connection").classList.remove("connected");$("connection").innerHTML='<span class="dot"></span> Перепідключення…';if(auth)setTimeout(()=>{if(auth)connectWs()},2500)};
  ws.onerror=()=>{$("connection").classList.remove("connected")};
  const heartbeat=setInterval(()=>{if(ws&&ws.readyState===WebSocket.OPEN)ws.send("ping");else clearInterval(heartbeat)},25000);
}
function setView(view){
  currentView=view;
  document.querySelectorAll(".view").forEach(el=>el.classList.add("hidden"));
  $(`view-${view}`)?.classList.remove("hidden");
  document.querySelectorAll(".nav-btn").forEach(el=>el.classList.toggle("active",el.dataset.view===view));
  $("view-kicker").textContent=viewMeta[view][0];$("view-title").textContent=viewMeta[view][1];
  if(view==="map")renderMap();
}
function renderAll(){if(!state)return;renderOverview();renderCharacters();renderMap();renderCombat();renderHistory();$("participant-count").textContent=`${state.participants.length} учасник${state.participants.length===1?"":"и"}`;$("campaign-title").textContent=state.session.name}
function renderOverview(){
  $("stat-players").textContent=state.participants.length;
  $("stat-characters").textContent=state.characters.length;
  const c=state.combat||{}, entries=c.initiative||[];
  $("stat-round").textContent=entries.length?c.round_no:"—";
  $("stat-turn").textContent=entries.length?`Хід: ${entries[c.active_index]?.name||"—"}`:"Бій не розпочато";
  const recent=state.events.slice(0,4);
  $("recent-events").innerHTML=recent.length?recent.map(eventHtml).join(""):'<div class="empty">Подій поки немає. Створи героя або кинь кубики.</div>';
}
function eventIcon(type){return ({dice_rolled:"◉",hp_changed:"♥",token_moved:"⌖",joined:"♙",attack:"⚔",turn_changed:"➜",session_created:"✦",character_created:"♙",character_updated:"✧",combat_started:"⚔",map_updated:"▦"})[type]||"✦"}
function eventHtml(e){const d=new Date(e.created_at*1000);return `<div class="event-item"><div class="event-symbol">${eventIcon(e.event_type)}</div><div><div class="event-message">${escapeHtml(e.message)}</div><div class="event-meta">${escapeHtml(e.actor)} · ${d.toLocaleString("uk-UA",{hour:"2-digit",minute:"2-digit",day:"2-digit",month:"short"})}</div></div></div>`}
function renderHistory(){
  $("all-events").innerHTML=state.events.length?state.events.map(eventHtml).join(""):'<div class="empty">Літопис ще порожній.</div>';
  const rolls=state.events.filter(e=>e.event_type==="dice_rolled");
  $("dice-history").innerHTML=rolls.length?rolls.slice(0,10).map(eventHtml).join(""):'<div class="empty">Тут з’являться результати кидків.</div>';
}
function isMine(ch){return auth.role==="GM"||(ch.owner_id===auth.participant_id&&ch.kind==="PLAYER")}
function charCard(ch){
  const pct=Math.max(0,Math.min(100,ch.hp/ch.max_hp*100));
  const attrs=[["STR",ch.str_stat],["DEX",ch.dex],["CON",ch.con],["INT",ch.int_stat],["WIS",ch.wis],["CHA",ch.cha]];
  return `<article class="character-card"><div class="character-top"><div class="character-portrait">${escapeHtml(ch.name.slice(0,1).toUpperCase())}</div><div><div class="character-name">${escapeHtml(ch.name)}</div><div class="character-kind">${ch.kind==="NPC"?"NPC · СТВОРІННЯ":`ГЕРОЙ · РІВЕНЬ ${ch.level}`}</div></div></div><div class="character-body"><div class="hp-line"><span>ЗДОРОВ’Я</span><strong>${ch.hp} / ${ch.max_hp} HP</strong></div><div class="hp-track"><div class="hp-fill" style="width:${pct}%"></div></div><div class="ac-row"><span>Клас захисту</span><strong>AC ${ch.ac}</strong></div><div class="attributes">${attrs.map(([n,v])=>`<div class="attribute"><span>${n}</span><strong>${v}</strong></div>`).join("")}</div>${ch.conditions?`<div class="condition">✧ ${escapeHtml(ch.conditions)}</div>`:""}<div class="char-actions">${auth.role==="GM"?`<button class="mini-btn" data-hp="${ch.id}" data-amount="-1">− HP</button><button class="mini-btn" data-hp="${ch.id}" data-amount="1">+ HP</button>`:""}${isMine(ch)?`<button class="mini-btn" data-edit="${ch.id}">Редагувати</button>`:""}</div></div></article>`;
}
function renderCharacters(){
  $("character-grid").innerHTML=state.characters.length?state.characters.map(charCard).join(""):'<div class="empty">У сесії ще немає героїв. Створи першого персонажа.</div>';
  $("character-grid").querySelectorAll("[data-hp]").forEach(b=>b.onclick=async()=>{try{await api(`/api/sessions/${auth.session_id}/characters/${b.dataset.hp}/hp`,{method:"POST",body:JSON.stringify({amount:Number(b.dataset.amount),actor:auth.name})});await loadState()}catch(e){toast(e.message)}});
  $("character-grid").querySelectorAll("[data-edit]").forEach(b=>b.onclick=()=>editCharacter(Number(b.dataset.edit)));
}
function renderMap(){
  if(!state)return;
  const map=$("battle-map");
  if(state.session.map_image){map.style.backgroundImage=`linear-gradient(#e1d2ba1c 1px,transparent 1px),linear-gradient(90deg,#e1d2ba1c 1px,transparent 1px),url("${state.session.map_image}")`;map.classList.add("has-image")}
  else{map.style.backgroundImage="";map.classList.remove("has-image")}
  $("map-tokens").innerHTML=state.characters.map(ch=>`<button class="token ${ch.kind==="NPC"?"npc":""} ${selectedToken===ch.id?"selected":""}" title="${escapeHtml(ch.name)}" style="grid-column:${ch.x+1};grid-row:${ch.y+1}" data-token="${ch.id}">${escapeHtml(ch.name.slice(0,2).toUpperCase())}<span>${escapeHtml(ch.name)}</span></button>`).join("");
  $("map-tokens").querySelectorAll("[data-token]").forEach(el=>{
    const id=Number(el.dataset.token),ch=state.characters.find(c=>c.id===id);
    el.onclick=()=>{selectedToken=id;renderMap();};
    el.ondblclick=async ev=>{ev.preventDefault();if(!isMine(ch)){toast("Можна рухати лише власного героя.");return}const x=Math.max(0,Math.min(11,ch.x+1));try{await api(`/api/sessions/${auth.session_id}/characters/${id}`,{method:"PATCH",body:JSON.stringify({x})});await loadState()}catch(e){toast(e.message)}};
    el.onpointerdown=ev=>{if(!isMine(ch)){return}el.setPointerCapture?.(ev.pointerId)};
  });
  map.onclick=async ev=>{
    if(ev.target.closest(".token"))return;
    if(!selectedToken)return;
    const ch=state.characters.find(c=>c.id===selectedToken);if(!ch||!isMine(ch)){toast("Можна рухати лише власного героя.");return}
    const rect=map.getBoundingClientRect(),x=Math.max(0,Math.min(11,Math.floor((ev.clientX-rect.left)/rect.width*12))),y=Math.max(0,Math.min(7,Math.floor((ev.clientY-rect.top)/rect.height*8)));
    try{await api(`/api/sessions/${auth.session_id}/characters/${ch.id}`,{method:"PATCH",body:JSON.stringify({x,y})});await loadState()}catch(e){toast(e.message)}
  };
  $("map-roster").innerHTML=state.characters.map(ch=>`<div class="legend-row"><span class="legend-icon ${ch.kind==="NPC"?"npc":""}">${escapeHtml(ch.name.slice(0,1).toUpperCase())}</span><span>${escapeHtml(ch.name)}<br><span class="muted">${ch.hp}/${ch.max_hp} HP</span></span></div>`).join("")||'<p class="muted tiny">Немає токенів</p>';
}
function renderCombat(){
  const combat=state.combat||{}, entries=combat.initiative||[];
  $("combat-round").textContent=entries.length?combat.round_no:"—";
  $("initiative-list").innerHTML=entries.length?entries.map((e,i)=>{const ch=state.characters.find(c=>c.id===e.character_id);return `<div class="initiative-item ${i===combat.active_index?"active":""}"><div class="initiative-score">${e.initiative}</div><div><div class="initiative-name">${escapeHtml(e.name)} ${i===combat.active_index?'<span style="color:var(--gold)">· ХІД</span>':""}</div><div class="initiative-hp">${ch?`${ch.hp}/${ch.max_hp} HP · AC ${ch.ac}`:""}</div></div><div class="initiative-hp">#${i+1}</div><div class="initiative-actions">${auth.role==="GM"&&ch?`<button class="mini-btn" data-hp="${ch.id}" data-amount="-1">− HP</button><button class="mini-btn" data-hp="${ch.id}" data-amount="1">+ HP</button>`:""}</div></div>`}).join(""):'<div class="empty">Бій не розпочато. GM може кинути ініціативу для всіх персонажів.</div>';
  $("initiative-list").querySelectorAll("[data-hp]").forEach(b=>b.onclick=async()=>{try{await api(`/api/sessions/${auth.session_id}/characters/${b.dataset.hp}/hp`,{method:"POST",body:JSON.stringify({amount:Number(b.dataset.amount),actor:auth.name})});await loadState()}catch(e){toast(e.message)}});
  const opts=state.characters.map(ch=>`<option value="${ch.id}">${escapeHtml(ch.name)}</option>`).join("");
  $("attacker-select").innerHTML=opts;$("target-select").innerHTML=opts;
}
function editCharacter(id){
  const ch=state.characters.find(c=>c.id===id);if(!ch)return;
  $("char-kind").value="EDIT";$("char-name").value=ch.name;$("char-level").value=ch.level;$("char-hp").value=ch.max_hp;$("char-ac").value=ch.ac;$("char-str").value=ch.str_stat;$("char-dex").value=ch.dex;$("char-con").value=ch.con;$("char-int").value=ch.int_stat;$("char-wis").value=ch.wis;$("char-cha").value=ch.cha;
  $("char-dialog-title").textContent="Редагувати персонажа";$("char-dialog-kicker").textContent="CHARACTER SHEET";$("character-form").dataset.editId=id;setError("char-error","");$("character-dialog").showModal();
}
function openCharacter(kind){
  $("character-form").reset();$("char-kind").value=kind;$("char-level").value=1;$("char-hp").value=12;$("char-ac").value=12;
  $("char-dialog-title").textContent=kind==="NPC"?"Створити NPC":"Створити героя";$("char-dialog-kicker").textContent=kind==="NPC"?"NON-PLAYER CHARACTER":"HERO CREATION";delete $("character-form").dataset.editId;setError("char-error","");$("character-dialog").showModal();
}
document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("active",t===b));$("create-form").classList.toggle("hidden",b.dataset.mode!=="create");$("join-form").classList.toggle("hidden",b.dataset.mode!=="join");setError("gate-error","")});
$("create-form").onsubmit=async ev=>{ev.preventDefault();setError("gate-error","");try{auth=await api("/api/sessions",{method:"POST",body:JSON.stringify({name:$("campaign-name").value,gm_name:$("gm-name").value})});state=await api(`/api/sessions/${auth.session_id}/state`);showGame();connectWs();toast("Сесію створено. Передай код гравцям.")}catch(e){setError("gate-error",e.message)}};
$("join-form").onsubmit=async ev=>{ev.preventDefault();setError("gate-error","");try{auth=await api("/api/join",{method:"POST",body:JSON.stringify({code:$("join-code").value,name:$("player-name").value})});state=await api(`/api/sessions/${auth.session_id}/state`);auth.participant_id=state.participants.find(p=>p.name===auth.name&&p.role==="PLAYER")?.id;showGame();connectWs();toast("Ти приєднався до пригоди.")}catch(e){setError("gate-error",e.message)}};
document.querySelectorAll(".nav-btn").forEach(b=>b.onclick=()=>setView(b.dataset.view));
document.querySelectorAll("[data-goto]").forEach(b=>b.onclick=()=>setView(b.dataset.goto));
$("leave").onclick=()=>showGate();
$("copy-code").onclick=async()=>{try{await navigator.clipboard.writeText(auth.code);toast("Код скопійовано")}catch(e){toast(`Код сесії: ${auth.code}`)}};
$("add-character").onclick=()=>openCharacter("PLAYER");$("add-npc").onclick=()=>openCharacter("NPC");
$("close-character").onclick=()=>$("character-dialog").close();
$("character-form").onsubmit=async ev=>{
  ev.preventDefault();setError("char-error","");
  const d={name:$("char-name").value,level:Number($("char-level").value),hp:Number($("char-hp").value),max_hp:Number($("char-hp").value),ac:Number($("char-ac").value),str_stat:Number($("char-str").value),dex:Number($("char-dex").value),con:Number($("char-con").value),int_stat:Number($("char-int").value),wis:Number($("char-wis").value),cha:Number($("char-cha").value)};
  try{
    if($("character-form").dataset.editId){
      const id=Number($("character-form").dataset.editId);const ch=state.characters.find(c=>c.id===id);
      await api(`/api/sessions/${auth.session_id}/characters/${id}`,{method:"PATCH",body:JSON.stringify({...d,hp:Math.min(ch.hp,d.max_hp)})});
    }else await api(`/api/sessions/${auth.session_id}/characters`,{method:"POST",body:JSON.stringify({...d,kind:$("char-kind").value})});
    $("character-dialog").close();await loadState();toast("Персонажа збережено");
  }catch(e){setError("char-error",e.message)}
};
$("roll-form").onsubmit=async ev=>{ev.preventDefault();try{const r=await api(`/api/sessions/${auth.session_id}/roll`,{method:"POST",body:JSON.stringify({actor:auth.name,formula:$("roll-formula").value})});$("dice-display").textContent=r.total;toast(`${r.formula}: ${r.rolls.join(" + ")}${r.modifier?r.modifier>0?" + "+r.modifier:" - "+Math.abs(r.modifier):""} = ${r.total}`);await loadState()}catch(e){toast(e.message)}};
$("start-combat").onclick=async()=>{if(!state.characters.length){toast("Спочатку створи персонажів.");return}try{const entries=state.characters.map(c=>({character_id:c.id,bonus:Math.floor((c.dex-10)/2)}));await api(`/api/sessions/${auth.session_id}/combat/start`,{method:"POST",body:JSON.stringify({entries})});await loadState();toast("Ініціативу визначено")}catch(e){toast(e.message)}};
$("next-turn").onclick=async()=>{try{const r=await api(`/api/sessions/${auth.session_id}/combat/next`,{method:"POST"});await loadState();toast(`Хід: ${r.active} · Раунд ${r.round}`)}catch(e){toast(e.message)}};
$("attack-btn").onclick=async()=>{try{const r=await api(`/api/sessions/${auth.session_id}/combat/attack`,{method:"POST",body:JSON.stringify({attacker_id:Number($("attacker-select").value),target_id:Number($("target-select").value),bonus:Number($("attack-bonus").value),damage:$("attack-damage").value})});await loadState();toast(`${r.hit?"Влучання!":"Промах."} Кидок ${r.attack_total}; шкода ${r.damage}`)}catch(e){toast(e.message)}};
$("map-upload").onchange=async ev=>{const file=ev.target.files[0];if(!file)return;const form=new FormData();form.append("file",file);try{await api(`/api/sessions/${auth.session_id}/map`,{method:"POST",body:form});await loadState();toast("Карту завантажено")}catch(e){toast(e.message)}ev.target.value=""};
$("clear-map").onclick=()=>toast("Щоб прибрати фон, поки що створіть нову сесію. Функція очищення карти буде додана в наступній версії.");
