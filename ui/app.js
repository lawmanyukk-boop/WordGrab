'use strict';
const $ = (s) => document.querySelector(s);
const SPK_COLORS = ['#3370FF','#14B8A6','#8B5CF6','#F59E0B','#EC4899','#10B981','#6366F1','#EF4444'];
let API = null;
let cur = null;           // 当前记录 {id,title,duration,speakers,segments}
let startTimer = 0;

const audio = $('#audio');

/* ---------- 工具 ---------- */
function fmt(sec){sec=Math.max(0,Math.floor(sec||0));const m=Math.floor(sec/60),s=sec%60;return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');}
function spkColor(i){return SPK_COLORS[i%SPK_COLORS.length];}
function spkName(i){return (cur&&cur.speakers&&cur.speakers[String(i)])||('说话人'+(i+1));}
function toast(msg){const t=$('#toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2200);}
function setBanner(text){const b=$('#liveBanner');b.textContent=text||'';b.classList.toggle('hidden',!text);}
function renderTranscriptKeepScroll(){
  const box=$('#transcript');
  const nearBottom=box.scrollHeight-box.scrollTop-box.clientHeight<80;
  const st=box.scrollTop;
  renderTranscript();
  box.scrollTop=nearBottom?box.scrollHeight:st;
}

/* ---------- 历史列表 ---------- */
async function loadHistory(){
  const items = await API.list_items();
  renderHistory(items);
}
function renderHistory(items){
  const q = $('#sideSearch').value.trim();
  const list = $('#historyList');
  list.innerHTML='';
  items.filter(x=>!q||x.title.includes(q)).forEach(x=>{
    const el=document.createElement('div');
    el.className='hist-item'+(cur&&cur.id===x.id?' active':'');
    el.innerHTML=`<div class="t">${escapeHtml(x.title)}</div>
      <div class="m"><span>${x.created||''}</span><span>${fmt(x.duration)}</span><span>${x.n_speakers}人</span></div>`;
    el.onclick=()=>openItem(x.id);
    list.appendChild(el);
  });
}
function escapeHtml(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

/* ---------- 打开一条 ---------- */
async function openItem(id, keepScroll){
  const sameItem = cur && cur.id === id;
  cur = await API.open_item(id);
  $('#empty').classList.add('hidden');
  $('#content').classList.remove('hidden');
  $('#docTitle').textContent = cur.title;
  $('#docMeta').textContent = cur.spk_pending
    ? `${fmt(cur.duration)} · 初稿`
    : `${fmt(cur.duration)} · ${Object.keys(cur.speakers).length}位说话人`;
  if(!sameItem){
    audio.src = cur.audio_url; audio.playbackRate = curSpeed;
    $('#curTime').textContent='00:00'; $('#seek').value=0;
    setPlayIcon(false);
  }
  $('#totTime').textContent = fmt(cur.duration);
  if(keepScroll) renderTranscriptKeepScroll(); else renderTranscript();
  if(cur.spk_pending){
    $('#spkBar').innerHTML='';
    setBanner('🔊 说话人分离进行中…文稿已可阅读，说话人稍后自动标注');
  }else{
    renderSpkBar();
    setBanner('');
  }
  await loadHistory();
}

/* ---------- 顶部说话人标签（点一下改全部） ---------- */
function renderSpkBar(){
  const bar=$('#spkBar'); if(!bar) return;
  bar.innerHTML='';
  const idxs=[...new Set(cur.segments.map(s=>s.spk))].sort((a,b)=>a-b);
  idxs.forEach(i=>{
    const chip=document.createElement('span');
    chip.className='spk-chip'; chip.style.background=spkColor(i); chip.dataset.spk=i;
    chip.title='点击改名（该说话人全部记录都会改）';
    chip.textContent=spkName(i).slice(-2);
    chip.onclick=async()=>{
      const name=prompt('把"'+spkName(i)+'"改名为：', spkName(i));
      if(name==null) return;
      const trimmed=name.trim(); if(!trimmed) return;
      cur.speakers[String(i)]=trimmed;
      await API.rename_speaker(cur.id,i,trimmed);
      renderTranscript(); renderSpkBar();
    };
    bar.appendChild(chip);
  });
}

function renderTranscript(){
  const box=$('#transcript'); box.innerHTML='';
  cur.segments.forEach((s,idx)=>{
    const color=spkColor(s.spk);
    const el=document.createElement('div');
    el.className='seg'; el.dataset.start=s.start; el.dataset.idx=idx;
    el.innerHTML=`
      <div class="seg-avatar" style="background:${color}">${spkName(s.spk).slice(-2)}</div>
      <div class="seg-body">
        <div class="seg-top">
          <span class="seg-name" style="color:${color}" data-spk="${s.spk}" title="双击改名">${escapeHtml(spkName(s.spk))}</span>
          <button class="seg-rename-btn" data-spk="${s.spk}" title="改名" aria-label="改名">✎</button>
          <span class="seg-time">${fmt(s.start/1000)}</span>
        </div>
        <div class="seg-text">${escapeHtml(s.text)}</div>
      </div>`;
    el.onclick=(e)=>{ if(e.target.closest('.seg-name,.seg-rename-btn'))return; seekTo(s.start/1000); };
    box.appendChild(el);
  });
  bindSpeakerRename();
  applyDocSearch();
}

/* ---------- 播放同步 ---------- */
function seekTo(sec){ audio.currentTime=sec; audio.play(); setPlayIcon(true); }
function setPlayIcon(playing){
  $('#playIcon').innerHTML = playing
    ? '<path fill="currentColor" d="M7 5h4v14H7zm6 0h4v14h-4z"/>'
    : '<path fill="currentColor" d="M8 5v14l11-7z"/>';
}
audio.addEventListener('timeupdate',()=>{
  const t=audio.currentTime;
  $('#curTime').textContent=fmt(t);
  if(cur&&cur.duration) $('#seek').value=Math.round(t/cur.duration*1000);
  // 高亮当前段
  const segs=[...document.querySelectorAll('.seg')];
  let activeEl=null;
  for(const el of segs){ if(t*1000+250>=+el.dataset.start) activeEl=el; else break; }
  segs.forEach(el=>el.classList.toggle('active',el===activeEl));
  if(activeEl && !activeEl._seen){segs.forEach(e=>e._seen=false);activeEl._seen=true;
    const r=activeEl.getBoundingClientRect(),b=$('#transcript').getBoundingClientRect();
    if(r.top<b.top+40||r.bottom>b.bottom-40)activeEl.scrollIntoView({block:'center',behavior:'smooth'});}
});
audio.addEventListener('ended',()=>setPlayIcon(false));
$('#playBtn').onclick=()=>{ if(audio.paused){audio.play();setPlayIcon(true);}else{audio.pause();setPlayIcon(false);} };
$('#seek').oninput=(e)=>{ if(cur)audio.currentTime=e.target.value/1000*cur.duration; };

/* 倍速 */
let curSpeed=1; const SPEEDS=[1,1.25,1.5,2];
$('#speedBtn').onclick=()=>{ curSpeed=SPEEDS[(SPEEDS.indexOf(curSpeed)+1)%SPEEDS.length]; audio.playbackRate=curSpeed; $('#speedBtn').textContent=curSpeed.toFixed(curSpeed%1?2:1).replace(/0$/,'')+'×'; };

/* ---------- 说话人 / 标题 改名 ---------- */
function enterRenameMode(el){
  el.contentEditable=true;el.focus();document.execCommand('selectAll',false,null);
}
function bindSpeakerRename(){
  if(cur && cur.live) return; // 识别中记录未落盘，改名接口还不可用
  document.querySelectorAll('.seg-name').forEach(el=>{
    el.ondblclick=(e)=>{e.stopPropagation();enterRenameMode(el);};
    el.onblur=async()=>{
      el.contentEditable=false;
      const spk=+el.dataset.spk, name=el.textContent.trim()||spkName(spk);
      cur.speakers[String(spk)]=name;
      await API.rename_speaker(cur.id,spk,name);
      renderTranscript();
    };
    el.onkeydown=(e)=>{if(e.key==='Enter'){e.preventDefault();el.blur();}};
  });
  document.querySelectorAll('.seg-rename-btn').forEach(btn=>{
    btn.onclick=(e)=>{
      e.stopPropagation();
      const nameEl=btn.previousElementSibling;
      enterRenameMode(nameEl);
    };
  });
}
$('#docTitle').ondblclick=()=>{const el=$('#docTitle');el.contentEditable=true;el.focus();document.execCommand('selectAll',false,null);};
$('#docTitle').onblur=async()=>{const el=$('#docTitle');el.contentEditable=false;const t=el.textContent.trim()||cur.title;cur.title=t;await API.rename_item(cur.id,t);loadHistory();};
$('#docTitle').onkeydown=(e)=>{if(e.key==='Enter'){e.preventDefault();e.target.blur();}};

/* ---------- 全文搜索 ---------- */
function applyDocSearch(){
  const q=$('#docSearch').value.trim();
  const texts=document.querySelectorAll('.seg-text');
  let n=0;
  texts.forEach(el=>{
    const raw=el.textContent;
    if(!q){el.innerHTML=escapeHtml(raw);return;}
    const parts=raw.split(q);
    n+=parts.length-1;
    el.innerHTML=parts.map(escapeHtml).join(`<mark>${escapeHtml(q)}</mark>`);
  });
  $('#searchCount').textContent = q?(n?`${n}处`:'无'):'';
}
$('#docSearch').oninput=applyDocSearch;
$('#sideSearch').oninput=loadHistory;

/* ---------- 导入 / 转写 ---------- */
async function doImport(){
  try{
    await API.log('导入按钮被点击');
    const path = await API.pick_file();
    await API.log('pick_file 返回: '+path);
    if(!path){ toast('未选择文件'); return; }
    const id = await API.start_transcribe(path);
    showProgress(path.split('/').pop());
    pollStatus(id);
  }catch(e){
    await API.log('doImport 出错: '+e);
    toast('出错: '+e);
  }
}
$('#importBtn').onclick=doImport;
$('#importBtn2').onclick=doImport;
$('#exportBtn').onclick=async()=>{const p=await API.export_txt(cur.id);if(p)toast('已导出：'+p.split('/').pop());};
$('#deleteBtn').onclick=async()=>{ if(!cur)return; await API.delete_item(cur.id); cur=null; $('#content').classList.add('hidden'); $('#empty').classList.remove('hidden'); loadHistory(); };

let progT0=0, progEst=0, progDur=0;
function showProgress(name){
  $('#empty').classList.add('hidden');$('#content').classList.add('hidden');
  $('#progress').classList.remove('hidden');
  $('#progTitle').textContent='正在转写：'+name;
  $('#progFill').classList.add('indet');$('#progFill').style.width='';
  progT0=Date.now(); progEst=0; progDur=0; clearInterval(startTimer);
  startTimer=setInterval(tickProgress,500);
}
function tickProgress(){
  const el=(Date.now()-progT0)/1000;
  let line='已用时 '+fmt(el);
  if(progEst>0){
    // 按预估时间驱动进度条，封顶 92%，完成时由 onDone 补满到 100%
    const fill=$('#progFill');
    fill.classList.remove('indet');
    fill.style.width=Math.round(Math.min(0.92,el/progEst)*100)+'%';
    line+=' · 预计还需 '+fmt(Math.max(0,progEst-el));
    if(progDur>0) line+='（音频 '+fmt(progDur)+'）';
  }
  $('#progElapsed').textContent=line;
}
/* 进度：前端轮询后端 /status/<iid>（后台线程只写状态，不从子线程回调 JS，避免 macOS 崩溃）*/
let pollTimer=0;
function applyProgress(p){
  $('#progStage').textContent=p.stage||'';
  if(p.info){ if(p.info.est_total) progEst=p.info.est_total; if(p.info.duration) progDur=p.info.duration; }
  if(progEst>0){ tickProgress(); return; } // 进度条改由 tickProgress 按预估时间驱动
  const fill=$('#progFill');
  if(p.pct==null){fill.classList.add('indet');fill.style.width='';}
  else{fill.classList.remove('indet');fill.style.width=Math.round(p.pct*100)+'%';}
}
function stopProgress(){ clearInterval(pollTimer); clearInterval(startTimer); }

/* 渐进出字：转写中就把已识别的段落实时铺到文稿区 */
function renderLive(iid, st){
  if(cur && cur.id!==iid && !cur.live) return;   // 用户在看别的记录，不打扰
  clearInterval(startTimer);                      // 收起进度遮罩，改用横幅
  $('#progress').classList.add('hidden');
  $('#empty').classList.add('hidden');
  $('#content').classList.remove('hidden');
  const dur=(st.info&&st.info.duration)||0;
  if(!cur || cur.id!==iid){
    cur={id:iid, title:st.title||'', duration:dur, speakers:{'0':'说话人'}, segments:[], live:true};
    $('#docTitle').textContent=cur.title;
    audio.src='/audio/'+iid; audio.playbackRate=curSpeed;
    $('#curTime').textContent='00:00'; $('#seek').value=0;
    setPlayIcon(false);
    $('#spkBar').innerHTML='';
  }
  if(dur){ cur.duration=dur; $('#totTime').textContent=fmt(dur); }
  $('#docMeta').textContent=`${fmt(cur.duration)} · 识别中…`;
  cur.segments=st.partial;
  renderTranscriptKeepScroll();
  setBanner('⏳ '+(st.stage||'识别中…')+'（已出 '+st.partial.length+' 段，可边出边读）');
}

function pollStatus(iid){
  clearInterval(pollTimer);
  let draftOpened=false;
  pollTimer=setInterval(async()=>{
    let st;
    try{ const r=await fetch('/status/'+iid,{cache:'no-store'}); st=await r.json(); }
    catch(e){ return; } // 暂时读不到，下个周期再试
    if(!st || st.status==='unknown') return;
    applyProgress(st);
    const viewing = !cur || cur.id===iid;
    if(st.status==='running' && st.partial && st.partial.length){
      renderLive(iid, st);
    }else if(st.status==='draft'){
      if(!draftOpened){
        draftOpened=true;
        clearInterval(startTimer);
        $('#progress').classList.add('hidden');
        await loadHistory();
        if(viewing) await openItem(iid, true);
        else toast('文稿已就绪，说话人分离中…');
      }
    }else if(st.status==='done'){
      stopProgress();
      $('#progress').classList.add('hidden');
      await loadHistory();
      if(viewing){ await openItem(iid, true); }
      toast('转写完成');
    }else if(st.status==='error'){
      stopProgress();
      $('#progress').classList.add('hidden');
      if(draftOpened && viewing){
        setBanner('⚠️ 说话人分离失败：'+(st.msg||'未知')+'（文稿不受影响）');
      }else if(viewing && !(cur&&cur.live)){
        $('#empty').classList.remove('hidden');
      }
      toast('出错：'+(st.msg||'未知'));
    }
  },900);
}

/* ---------- 拖拽入窗口 ---------- */
const DROP_OK=/\.(m4a|mp3|wav|aac|mp4|mov|m4v|flac|ogg)$/i;
function showDropHint(on){ $('#dropHint').classList.toggle('hidden',!on); }
function initDrop(){
  let depth=0;
  const stop=e=>{e.preventDefault();e.stopPropagation();};
  window.addEventListener('dragenter',e=>{stop(e);depth++;showDropHint(true);});
  window.addEventListener('dragover',stop);
  window.addEventListener('dragleave',e=>{stop(e);depth=Math.max(0,depth-1);if(!depth)showDropHint(false);});
  window.addEventListener('drop',async e=>{
    stop(e); depth=0; showDropHint(false);
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if(!f){ toast('没读到文件'); return; }
    if(!DROP_OK.test(f.name)){ toast('不支持的文件类型：'+f.name); return; }
    showProgress(f.name);
    try{
      const resp = await fetch('/upload?name='+encodeURIComponent(f.name),{method:'POST',body:f});
      if(!resp.ok) throw new Error('HTTP '+resp.status);
      const j = await resp.json();
      pollStatus(j.id); // 转写进度由前端轮询 /status
    }catch(err){
      stopProgress();
      $('#progress').classList.add('hidden'); $('#empty').classList.remove('hidden');
      toast('上传失败：'+err);
    }
  });
}

/* ---------- 启动 ---------- */
function boot(){
  API=window.pywebview.api;
  initDrop();
  loadHistory();
}
if(window.pywebview&&window.pywebview.api) boot();
else window.addEventListener('pywebviewready',boot);
