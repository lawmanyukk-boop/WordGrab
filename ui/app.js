'use strict';
const $ = (s) => document.querySelector(s);
const SPK_COLORS = [
  '#FF675B','#FF9F1C','#F59E0B','#FFD23F',
  '#84CC16','#10B981','#14B8A6','#06B6D4',
  '#3A86FF','#6366F1','#8338EC','#A855F7',
  '#D946EF','#EC4899','#F43F5E','#EF4444',
];
let API = null;
let cur = null;           // 当前记录 {id,title,duration,speakers,segments}
let startTimer = 0;
let appSettings = {};
let skipSeconds = 15;

const DEFAULT_APP_SETTINGS={
  theme:'aurora-sea',reopen_last:true,auto_open_import:true,default_speed:1,skip_seconds:15,
  auto_diarization:true,transcription_mode:'accuracy',export_format:'txt',export_directory:'',
  filename_rule:'source_date',font_size:'standard',list_density:'standard',appearance:'light',follow_system:false,
  delete_audio_with_transcript:true,last_item_id:''
};

const THEMES=[
  ['aurora-sea','极光海','linear-gradient(145deg,#9BE5D2,#55A4DA)'],
  ['solar-bloom','日光珊瑚','linear-gradient(145deg,#5C9ED6,#F4C56F 52%,#EB5A4E)'],
  ['lavender-haze','薰衣草雾','linear-gradient(145deg,#CDE9F5,#E5B6DF 52%,#B88ED8)'],
  ['tide-ember','潮汐余晖','linear-gradient(145deg,#1168B8,#8599C3 48%,#F0663D)'],
  ['midnight-prism','午夜棱镜','linear-gradient(145deg,#111A35,#383171 52%,#7E316E)'],
  ['matcha-mist','抹茶晨雾','linear-gradient(145deg,#E0EEB9,#9DCEB0 52%,#5DA88F)'],
  ['rose-quartz','玫瑰石英','linear-gradient(145deg,#F4D2C9,#DE91AA 52%,#D185A4)'],
  ['sandstone-glow','琥珀沙丘','linear-gradient(145deg,#F5DDAA,#DD976D 52%,#AF5346)'],
  ['deep-ocean','深海蓝','linear-gradient(145deg,#053C5B,#0788A5 52%,#17387B)'],
  ['graphite-pearl','石墨银','linear-gradient(145deg,#5D626B,#292E36 52%,#10141A)'],
  ['rainbow-glow','虹光混色','linear-gradient(145deg,#4DD3C5,#F5D963 28%,#F1767B 54%,#AE6FD0 76%,#5D9FE2)'],
];

const audio = $('#audio');

/* ---------- 工具 ---------- */
function fmt(sec){sec=Math.max(0,Math.floor(sec||0));const m=Math.floor(sec/60),s=sec%60;return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');}
function spkColor(i){
  const key=String(i);
  if(cur&&cur.speaker_colors&&cur.speaker_colors[key]) return cur.speaker_colors[key];
  const index=Math.abs(Number(i)||0)%SPK_COLORS.length;
  return SPK_COLORS[index];
}
function spkName(i){return (cur&&cur.speakers&&cur.speakers[String(i)])||('说话人'+(i+1));}
let toastTimer=0;
function toast(msg,action){const t=$('#toast'),text=$('#toastText'),button=$('#toastAction');text.textContent=msg;button.classList.toggle('hidden',!action);button.onclick=async()=>{if(action)await action();t.classList.remove('show');};t.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('show'),action?5000:2200);}
function setBanner(text){const b=$('#liveBanner');b.textContent=text||'';b.classList.toggle('hidden',!text);}
function renderTranscriptKeepScroll(){
  const box=$('#transcript');
  const nearBottom=box.scrollHeight-box.scrollTop-box.clientHeight<80;
  const st=box.scrollTop;
  renderTranscript();
  box.scrollTop=nearBottom?box.scrollHeight:st;
}

/* ---------- 设置中心 ---------- */
function themeInfo(key){return THEMES.find(theme=>theme[0]===key)||THEMES[0];}

function applyTheme(key){
  const theme=themeInfo(key);
  document.documentElement.dataset.theme=theme[0];
  document.querySelectorAll('.settings-theme').forEach(button=>{
    const active=button.dataset.theme===theme[0];
    button.classList.toggle('active',active);
    button.setAttribute('aria-pressed',active?'true':'false');
  });
  try{localStorage.setItem('wordgrab-theme',theme[0]);}catch(_){ }
  const label=$('#currentThemeLabel');
  if(label) label.textContent=`${theme[1]} · ${THEMES.length}种配色`;
  return theme;
}

function setSettingsOpen(open){
  $('#settingsOverlay').classList.toggle('hidden',!open);
  $('#settingsBtn').setAttribute('aria-expanded',open?'true':'false');
  if(open){
    showSettingsPage('general');
    refreshSystemInfo();
    requestAnimationFrame(()=>$('.settings-nav-item.active').focus());
  }else{
    $('#settingsConfirm').classList.add('hidden');
  }
}

async function selectTheme(key){
  const theme=applyTheme(key);
  await saveSetting('theme',theme[0]);
}

const systemAppearance=window.matchMedia('(prefers-color-scheme: dark)');
const SETTINGS_META={
  general:['通用','调整日常使用习惯'],
  transcription:['转写与导出','设置处理方式与文件输出'],
  appearance:['外观','调整界面阅读体验'],
  themes:['主题','选择一款喜欢的界面配色'],
  storage:['存储与隐私','管理本地数据和模型'],
  about:['关于','WordGrab版本与运行状态'],
};

function formatBytes(bytes){
  const n=Number(bytes)||0;
  if(n<1024) return n+' B';
  if(n<1024**2) return (n/1024).toFixed(1)+' KB';
  if(n<1024**3) return (n/1024**2).toFixed(1)+' MB';
  return (n/1024**3).toFixed(1)+' GB';
}

function applySkipSeconds(seconds){
  skipSeconds=[5,10,15,30].includes(Number(seconds))?Number(seconds):15;
  [['#rewindBtn','后退'],['#forwardBtn','前进']].forEach(([selector,label])=>{
    const button=$(selector);
    button.querySelector('span').textContent=skipSeconds;
    button.setAttribute('aria-label',`${label}${skipSeconds}秒`);
    button.title=`${label}${skipSeconds}秒`;
  });
}

function applyPersistentSettings(){
  applyTheme(appSettings.theme);
  document.documentElement.dataset.fontSize=appSettings.font_size||'standard';
  document.documentElement.dataset.density=appSettings.list_density||'standard';
  const mode=appSettings.appearance||(appSettings.follow_system?'system':'light');
  const dark=mode==='dark'||(mode==='system'&&systemAppearance.matches);
  document.documentElement.dataset.appearance=dark?'dark':'light';
  selectSpeed(Number(appSettings.default_speed)||1);
  applySkipSeconds(appSettings.skip_seconds);
}

function syncSettingControls(){
  document.querySelectorAll('[data-setting]').forEach(control=>{
    const key=control.dataset.setting;
    if(!(key in appSettings)) return;
    if(control.type==='checkbox') control.checked=Boolean(appSettings[key]);
    else control.value=String(appSettings[key]);
  });
  const appearance=appSettings.appearance||(appSettings.follow_system?'system':'light');
  document.querySelectorAll('[data-appearance-option]').forEach(button=>{
    const active=button.dataset.appearanceOption===appearance;
    button.classList.toggle('active',active);
    button.setAttribute('aria-checked',active?'true':'false');
  });
  const directory=$('#exportDirectoryLabel');
  directory.textContent=appSettings.export_directory||'文稿';
  directory.title=appSettings.export_directory||'';
  const exportNames={docx:'Word',pdf:'PDF',txt:'TXT'};
  $('#exportBtn').textContent=`导出 ${exportNames[appSettings.export_format]||'TXT'}`;
  document.querySelectorAll('.export-option').forEach(option=>option.classList.toggle('default',option.dataset.format===appSettings.export_format));
}

async function saveSetting(key,value){
  appSettings={...appSettings,[key]:value};
  applyPersistentSettings();
  syncSettingControls();
  try{
    if(API&&API.update_settings){
      const saved=await API.update_settings({[key]:value});
      if(saved) appSettings={...DEFAULT_APP_SETTINGS,...saved};
    }else if(key==='theme'&&API&&API.set_theme){
      await API.set_theme(value);
    }
  }catch(_){toast('设置暂时无法保存');}
}

function showSettingsPage(page){
  document.querySelectorAll('.settings-page').forEach(section=>section.classList.toggle('active',section.dataset.settingsContent===page));
  const navPage=page==='themes'?'appearance':page;
  document.querySelectorAll('.settings-nav-item').forEach(button=>button.classList.toggle('active',button.dataset.settingsPage===navPage));
  const [title,subtitle]=SETTINGS_META[page]||SETTINGS_META.general;
  $('#settingsTitle').textContent=title;
  $('#settingsSubtitle').textContent=subtitle;
  $('.settings-scroll').scrollTop=0;
}

async function refreshSystemInfo(){
  if(!API||!API.get_system_info) return;
  try{
    const info=await API.get_system_info();
    $('#dataPathLabel').textContent=info.data_path||'—'; $('#dataPathLabel').title=info.data_path||'';
    $('#dataSizeLabel').textContent=formatBytes(info.data_size);
    $('#modelPathLabel').textContent=info.model_path||'—'; $('#modelPathLabel').title=info.model_path||'';
    $('#modelSizeLabel').textContent=formatBytes(info.model_size);
    $('#versionLabel').textContent='版本 '+(info.version||'—');
    $('#ffmpegPathLabel').textContent=info.ffmpeg_path||'—'; $('#ffmpegPathLabel').title=info.ffmpeg_path||'';
    $('#ffmpegStatus').textContent=info.ffmpeg_ok?'正常':'未找到'; $('#ffmpegStatus').className='status-value '+(info.ffmpeg_ok?'ok':'bad');
    $('#modelStatus').textContent=info.model_ready?'已安装':'需要下载'; $('#modelStatus').className='status-value '+(info.model_ready?'ok':'bad');
  }catch(_){toast('暂时无法读取存储信息');}
}

function confirmSettingsAction(title,message,confirmLabel='确认'){
  return new Promise(resolve=>{
    const dialog=$('#settingsConfirm');
    $('#confirmTitle').textContent=title;
    $('#confirmMessage').textContent=message;
    $('#confirmAccept').textContent=confirmLabel;
    dialog.classList.remove('hidden');
    const finish=value=>{dialog.classList.add('hidden');resolve(value);};
    $('#confirmCancel').onclick=()=>finish(false);
    $('#confirmAccept').onclick=()=>finish(true);
    requestAnimationFrame(()=>$('#confirmCancel').focus());
  });
}

async function initSettingsCenter(){
  const grid=$('#themeGrid');
  grid.innerHTML='';
  THEMES.forEach(([key,label,swatch])=>{
    const button=document.createElement('button');
    button.type='button';
    button.className='settings-theme';
    button.dataset.theme=key;
    button.setAttribute('aria-label',label);
    button.setAttribute('aria-pressed','false');
    button.innerHTML=`<span class="settings-theme-swatch" style="--swatch:${swatch}"></span><span class="settings-theme-label">${label}</span>`;
    button.onclick=()=>selectTheme(key);
    grid.appendChild(button);
  });

  let saved={};
  try{
    if(API&&API.get_settings){
      saved=await API.get_settings()||{};
    }
  }catch(_){ }
  const requested=new URLSearchParams(location.search).get('theme');
  appSettings={...DEFAULT_APP_SETTINGS,...saved};
  if(requested) appSettings.theme=requested;
  applyPersistentSettings();
  syncSettingControls();

  $('#settingsBtn').onclick=(event)=>{
    event.stopPropagation();
    setSettingsOpen($('#settingsBtn').getAttribute('aria-expanded')!=='true');
  };
  $('#settingsClose').onclick=()=>setSettingsOpen(false);
  $('#settingsOverlay').onclick=event=>{if(event.target===$('#settingsOverlay'))setSettingsOpen(false);};
  document.querySelectorAll('.settings-nav-item').forEach(button=>button.onclick=()=>showSettingsPage(button.dataset.settingsPage));
  $('#themeDrillBtn').onclick=()=>showSettingsPage('themes');
  $('#themeBackBtn').onclick=()=>showSettingsPage('appearance');
  document.querySelectorAll('[data-setting]').forEach(control=>{
    control.onchange=()=>{
      const key=control.dataset.setting;
      let value=control.type==='checkbox'?control.checked:control.value;
      if(['default_speed','skip_seconds'].includes(key)) value=Number(value);
      saveSetting(key,value);
    };
  });
  document.querySelectorAll('[data-appearance-option]').forEach(button=>{
    button.onclick=()=>saveSetting('appearance',button.dataset.appearanceOption);
  });
  const onSystemAppearanceChange=()=>{
    if((appSettings.appearance||(appSettings.follow_system?'system':'light'))==='system') applyPersistentSettings();
  };
  if(systemAppearance.addEventListener) systemAppearance.addEventListener('change',onSystemAppearanceChange);
  else if(systemAppearance.addListener) systemAppearance.addListener(onSystemAppearanceChange);
  $('#chooseExportDirectory').onclick=async()=>{
    const directory=await API.pick_export_directory();
    if(directory){appSettings.export_directory=directory;syncSettingControls();toast('默认保存位置已更新');}
  };
  $('#changeDataFolder').onclick=async()=>{
    const directory=await API.pick_data_directory();
    if(!directory)return;
    const confirmed=await confirmSettingsAction(
      '更改保存位置',
      `现有文稿、录音和设置将移动到：${directory}。请选择空文件夹，移动完成前不要退出 WordGrab。`,
      '移动数据',
    );
    if(!confirmed)return;
    const button=$('#changeDataFolder');
    const originalLabel=button.textContent;
    button.disabled=true;
    button.textContent='正在移动…';
    try{
      const result=await API.set_data_directory(directory);
      if(!result||!result.ok){toast(result&&result.message||'更改保存位置失败');return;}
      await refreshSystemInfo();
      toast(result.message||'保存位置已更新');
    }catch(_){
      toast('更改保存位置失败，仍在使用原位置');
    }finally{
      button.disabled=false;
      button.textContent=originalLabel;
    }
  };
  $('#openDataFolder').onclick=()=>API.open_local_resource('data');
  $('#openModelsFolder').onclick=()=>API.open_local_resource('models');
  $('#openReadmeBtn').onclick=()=>API.open_local_resource('readme');
  $('#openLicenseBtn').onclick=()=>API.open_local_resource('license');
  $('#clearHistoryBtn').onclick=async()=>{
    const extra=appSettings.delete_audio_with_transcript?'录音和文稿都会被删除。':'原始录音会移动到“保留的录音”文件夹。';
    if(!await confirmSettingsAction('清理全部转写记录',`这个操作无法撤销。${extra}`,'清理记录'))return;
    const result=await API.clear_history();
    if(!result||!result.ok){toast(result&&result.message||'清理失败');return;}
    cur=null;audio.pause();audio.removeAttribute('src');$('#content').classList.add('hidden');$('#empty').classList.remove('hidden');
    await loadHistory();await refreshSystemInfo();toast(`已清理 ${result.count||0} 条记录`);
  };
  $('#clearModelBtn').onclick=async()=>{
    if(!await confirmSettingsAction('清理模型缓存','清理后下次转写需要重新下载约3GB模型文件。','清理模型'))return;
    const result=await API.clear_model_cache();
    if(!result||!result.ok){toast(result&&result.message||'清理失败');return;}
    await refreshSystemInfo();toast(`已释放 ${formatBytes(result.freed)}`);
  };
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!$('#settingsOverlay').classList.contains('hidden'))setSettingsOpen(false);
  });
}

/* ---------- 历史列表 ---------- */
async function loadHistory(){
  const items = await API.list_items();
  if(window.historySortOldest) items.reverse();
  window.historyItems=items;
  renderHistory(items);
}
function renderHistory(items){
  const q = $('#sideSearch').value.trim();
  const list = $('#historyList');
  list.innerHTML='';
  items.filter(x=>(!q||x.title.includes(q))).forEach((x,index)=>{
    const el=document.createElement('button');
    el.type='button';
    el.className='hist-item'+(cur&&cur.id===x.id?' active':'');
    el.title=x.title||'';
    el.innerHTML=`${window.historyManageMode?`<input class="hist-check" type="checkbox" data-id="${x.id}" aria-label="选择${escapeHtml(x.title)}">`:''}<span class="hist-index">${String(index+1).padStart(2,'0')}</span>
      <span class="hist-copy"><span class="t">${escapeHtml(x.title)}</span>
      <span class="m"><span title="${escapeHtml(x.error||'')}">${x.status==='error'?'失败':x.status==='running'?'处理中':x.created||''}</span><span>${fmt(x.duration)}</span><span>${x.n_speakers}人</span></span></span>`;
    el.onclick=event=>{if(event.target.closest('.hist-check'))return;if(window.historyManageMode)return;openItem(x.id);};
    el.querySelector('.hist-check')?.addEventListener('change',updateHistorySelection);
    list.appendChild(el);
  });
  updateHistorySelection();
}
function escapeHtml(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

/* ---------- 打开一条 ---------- */
async function openItem(id, keepScroll){
  const sameItem = cur && cur.id === id;
  cur = await API.open_item(id);
  appSettings.last_item_id=id;
  if(API.update_settings) API.update_settings({last_item_id:id}).catch(()=>{});
  $('#empty').classList.add('hidden');
  $('#content').classList.remove('hidden');
  $('#docTitle').textContent = cur.title;
  $('#docEyebrow').textContent = `${cur.created||'今天'} · 本地文稿`;
  $('#docMeta').textContent = cur.spk_pending
    ? `${fmt(cur.duration)} · 初稿`
    : `${fmt(cur.duration)} · ${Object.keys(cur.speakers).length}位说话人`;
  $('#docStatus').textContent=cur.spk_pending?'处理中':'已完成';
  $('#fileFormat').textContent=cur.audio_format||'音频';
  $('#fileDuration').textContent=fmt(cur.duration);
  if(!sameItem){
    selectSpeed(Number(appSettings.default_speed)||1);
    if(cur.audio_url) audio.src=cur.audio_url; else audio.removeAttribute('src');
    audio.playbackRate = curSpeed;
    $('#curTime').textContent='00:00'; $('#seek').value=0;
    setPlayIcon(false);
  }
  $('#totTime').textContent = fmt(cur.duration);
  if(keepScroll) renderTranscriptKeepScroll(); else renderTranscript();
  renderSpkBar();
  if(cur.spk_pending){
    setBanner(appSettings.auto_diarization
      ? '说话人分离进行中…文稿已可阅读，说话人稍后自动标注'
      : '精细校对进行中…文稿已可阅读，完成后自动更新');
    const retry=document.createElement('button'); retry.type='button'; retry.className='retry-btn'; retry.textContent='仅重新分离说话人';
    retry.onclick=async()=>{retry.disabled=true;const result=await API.retry_diarization(cur.id);if(result&&result.ok){showProgress(cur.title);pollStatus(cur.id);}else{retry.disabled=false;toast(result&&result.message||'无法重新分离');}};
    $('#liveBanner').append(' ',retry);
  }else{
    setBanner('');
  }
  setView('transcript');
  await loadHistory();
}

/* ---------- 顶部说话人标签（点一下改全部） ---------- */
function renderSpkBar(){
  const bar=$('#spkBar'); if(!bar) return;
  bar.innerHTML='';
  const list=$('#speakerList'); if(list) list.innerHTML='';
  const idxs=[...new Set(cur.segments.map(s=>s.spk))].sort((a,b)=>a-b);
  idxs.forEach(i=>{
    const chip=document.createElement('span');
    chip.className='spk-chip'; chip.style.background=spkColor(i); chip.dataset.spk=i;
    chip.title='点击改名（该说话人全部记录都会改）';
    chip.textContent=spkName(i).slice(-2);
    chip.onclick=()=>renameSpeakerByPrompt(i);
    bar.appendChild(chip);

    if(list){
      const row=document.createElement('button');
      row.type='button'; row.className='speaker-row';
      row.innerHTML=`<span class="speaker-row-avatar" style="background:${spkColor(i)}">${escapeHtml(spkName(i).slice(-1))}</span>
        <span><strong>${escapeHtml(spkName(i))}</strong><small>说话人 ${i+1} · 点击改名</small></span>`;
      row.onclick=()=>renameSpeakerByPrompt(i);
      list.appendChild(row);
    }
  });
}

async function renameSpeakerByPrompt(i){
  if(cur&&cur.live) return;
  const name=prompt('把"'+spkName(i)+'"改名为：',spkName(i));
  if(name==null) return;
  const trimmed=name.trim(); if(!trimmed) return;
  cur.speakers[String(i)]=trimmed;
  await API.rename_speaker(cur.id,i,trimmed);
  renderTranscript(); renderSpkBar();
}

function renderTranscript(){
  const box=$('#transcript'); box.innerHTML='';
  cur.segments.forEach((s,idx)=>{
    const color=spkColor(s.spk);
    const el=document.createElement('div');
    el.className='seg'+(idx===0&&audio.currentTime<.25?' active':''); el.dataset.start=s.start; el.dataset.idx=idx; el.tabIndex=0; el.setAttribute('role','button'); el.setAttribute('aria-label',`${spkName(s.spk)}，${fmt(s.start/1000)}，按回车播放`);
    el.innerHTML=`
      <div class="seg-avatar" style="background:${color}">${spkName(s.spk).slice(-2)}</div>
      <div class="seg-body">
        <div class="seg-top">
          <span class="seg-name" style="color:${color}" data-spk="${s.spk}" title="双击改名">${escapeHtml(spkName(s.spk))}</span>
          <button class="seg-rename-btn" data-spk="${s.spk}" title="改名" aria-label="改名">改名</button>
          <span class="seg-time">${fmt(s.start/1000)}</span>
        </div>
        <div class="seg-text" contenteditable="true" spellcheck="false" data-idx="${idx}" title="点击编辑，失焦后保存">${escapeHtml(s.text)}</div>
      </div>`;
    el.onclick=(e)=>{ if(e.target.closest('.seg-name,.seg-rename-btn,.seg-text'))return; seekTo(s.start/1000); };
    el.onkeydown=e=>{if((e.key==='Enter'||e.key===' ')&&!e.target.closest('.seg-text,.seg-name,.seg-rename-btn')){e.preventDefault();seekTo(s.start/1000);}};
    box.appendChild(el);
  });
  bindSpeakerRename();
  box.querySelectorAll('.seg-text').forEach(el=>{
    el.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();el.blur();}};
    el.onblur=async()=>{
      const text=el.textContent.trim();
      const index=Number(el.dataset.idx);
      if(!text||text===cur.segments[index].text)return;
      cur.segments[index].text=text;
      try{await API.update_segment(cur.id,index,text);toast('文稿已保存');}
      catch(_){toast('文稿保存失败');}
    };
  });
  applyDocSearch();
}

/* ---------- 播放同步 ---------- */
function seekTo(sec){ audio.currentTime=sec; audio.play(); setPlayIcon(true); }
function setPlayIcon(playing){
  $('#playBtn').setAttribute('aria-label',playing?'暂停':'播放');
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

function skipAudio(seconds){
  if(!cur) return;
  const end=Number.isFinite(audio.duration)?audio.duration:(cur.duration||0);
  try{audio.currentTime=Math.max(0,Math.min(end,audio.currentTime+seconds));}catch(_){return;}
  $('#curTime').textContent=fmt(audio.currentTime);
  if(cur.duration) $('#seek').value=Math.round(audio.currentTime/cur.duration*1000);
}
$('#rewindBtn').onclick=()=>skipAudio(-skipSeconds);
$('#forwardBtn').onclick=()=>skipAudio(skipSeconds);

/* 音量 */
const volumeBtn=$('#volumeBtn');
const volumePanel=$('#volumePanel');
const volumeSlider=$('#volumeSlider');
function setVolumePanelOpen(open){
  volumePanel.classList.toggle('hidden',!open);
  volumeBtn.setAttribute('aria-expanded',open?'true':'false');
  if(open) requestAnimationFrame(()=>volumeSlider.focus());
}
function setVolume(value){
  const volume=Math.max(0,Math.min(1,Number(value)));
  audio.volume=volume;
  audio.muted=false;
  volumeSlider.value=String(volume);
  const percent=Math.round(volume*100);
  $('#volumeValue').textContent=percent+'%';
  volumeBtn.setAttribute('aria-label','调节音量，当前'+percent+'%');
  volumeBtn.classList.toggle('muted',volume===0);
}
volumeBtn.onclick=event=>{
  event.stopPropagation();
  setVolumePanelOpen(volumeBtn.getAttribute('aria-expanded')!=='true');
};
volumePanel.onclick=event=>event.stopPropagation();
volumeSlider.oninput=event=>setVolume(event.target.value);
setVolume(1);

/* 倍速 */
let curSpeed=1;
const speedBtn=$('#speedBtn');
const speedMenu=$('#speedMenu');
const speedOptions=[...document.querySelectorAll('.speed-option')];

function speedLabel(speed){return (speed===1?'1.0':String(speed))+'×';}
function setSpeedMenuOpen(open,{focusCurrent=false}={}){
  speedMenu.classList.toggle('hidden',!open);
  speedBtn.setAttribute('aria-expanded',open?'true':'false');
  if(open&&focusCurrent){
    const active=speedOptions.find(option=>Number(option.dataset.speed)===curSpeed);
    if(active) requestAnimationFrame(()=>active.focus());
  }
}
function selectSpeed(speed){
  curSpeed=speed;
  audio.playbackRate=curSpeed;
  speedBtn.textContent=speedLabel(curSpeed);
  speedOptions.forEach(option=>{
    const active=Number(option.dataset.speed)===curSpeed;
    option.classList.toggle('active',active);
    option.setAttribute('aria-checked',active?'true':'false');
  });
  setSpeedMenuOpen(false);
}

speedBtn.onclick=event=>{
  event.stopPropagation();
  setSpeedMenuOpen(speedBtn.getAttribute('aria-expanded')!=='true');
};
speedOptions.forEach(option=>{
  option.onclick=event=>{
    event.stopPropagation();
    selectSpeed(Number(option.dataset.speed));
    speedBtn.focus();
  };
});
speedMenu.onkeydown=event=>{
  if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
  event.preventDefault();
  const current=Math.max(0,speedOptions.indexOf(document.activeElement));
  let next=current;
  if(event.key==='ArrowDown') next=(current+1)%speedOptions.length;
  if(event.key==='ArrowUp') next=(current-1+speedOptions.length)%speedOptions.length;
  if(event.key==='Home') next=0;
  if(event.key==='End') next=speedOptions.length-1;
  speedOptions[next].focus();
};
document.addEventListener('click',event=>{
  if(!speedMenu.classList.contains('hidden')&&!event.target.closest('#speedControl')) setSpeedMenuOpen(false);
  if(!volumePanel.classList.contains('hidden')&&!event.target.closest('#volumeControl')) setVolumePanelOpen(false);
  if(!$('#exportMenu').classList.contains('hidden')&&!event.target.closest('#exportControl')) setExportMenuOpen(false);
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){
    if(!speedMenu.classList.contains('hidden')){setSpeedMenuOpen(false);speedBtn.focus();}
    if(!volumePanel.classList.contains('hidden')){setVolumePanelOpen(false);volumeBtn.focus();}
    if(!$('#exportMenu').classList.contains('hidden')){setExportMenuOpen(false);$('#exportBtn').focus();}
  }
  const modal=$('#settingsConfirm').classList.contains('hidden')?($('#settingsOverlay').classList.contains('hidden')?null:$('#settingsCenter')):$('#settingsConfirm');
  if(event.key==='Tab'&&modal){const focusable=[...modal.querySelectorAll('button,input,select,[tabindex]:not([tabindex="-1"])')].filter(el=>!el.disabled&&el.offsetParent!==null);if(focusable.length){const first=focusable[0],last=focusable[focusable.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}}
});
selectSpeed(curSpeed);

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
  document.querySelectorAll('.seg-text mark').forEach(mark=>mark.removeAttribute('data-search-index'));
}
$('#docSearch').oninput=applyDocSearch;
$('#sideSearch').oninput=loadHistory;
$('#historySort').onclick=()=>{window.historySortOldest=!window.historySortOldest;$('#historySort').textContent=window.historySortOldest?'最早':'最新';loadHistory();};
$('#historyManage').onclick=()=>{window.historyManageMode=!window.historyManageMode;$('#historyManage').textContent=window.historyManageMode?'完成':'批量选择';$('#historyBatchBar').classList.toggle('hidden',!window.historyManageMode);renderHistory(window.historyItems||[]);};
function updateHistorySelection(){const selected=[...document.querySelectorAll('.hist-check:checked')].map(input=>input.dataset.id);window.historySelected=selected;$('#historySelected').textContent=`已选 ${selected.length} 条`;}
$('#historyExport').onclick=async()=>{const ids=window.historySelected||[];if(!ids.length)return toast('请先选择文稿');const format=(prompt('选择导出格式：txt / pdf / docx','txt')||'').trim().toLowerCase();if(!['txt','pdf','docx'].includes(format))return toast('不支持的导出格式');const result=await API.bulk_export_items(ids,format);if(result&&result.ok)toast(`已批量导出 ${result.count} 份文稿`);else toast(result&&result.message||'批量导出失败');};
$('#historyDelete').onclick=async()=>{const ids=window.historySelected||[];if(!ids.length)return toast('请先选择文稿');if(!await confirmSettingsAction('删除所选文稿',`将 ${ids.length} 条记录移入最近删除。批量删除暂不支持一次性撤销。`,'删除'))return;const result=await API.bulk_delete_items(ids);if(result&&result.ok){window.historySelected=[];await loadHistory();toast(`已删除 ${result.count} 条记录`);}};

/* ---------- 文稿 / 说话人 / 文件信息 ---------- */
function setView(view){
  $('#readingGrid').dataset.view=view;
  document.querySelectorAll('.view-tab').forEach(tab=>{
    const active=tab.dataset.view===view;
    tab.classList.toggle('active',active);
    tab.setAttribute('aria-selected',active?'true':'false');
  });
  $('.mini-search').classList.toggle('hidden',view!=='transcript');
}
document.querySelectorAll('.view-tab').forEach(tab=>tab.onclick=()=>setView(tab.dataset.view));

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
function setExportMenuOpen(open){$('#exportMenu').classList.toggle('hidden',!open);$('#exportBtn').setAttribute('aria-expanded',open?'true':'false');}
$('#exportBtn').onclick=event=>{event.stopPropagation();setExportMenuOpen($('#exportBtn').getAttribute('aria-expanded')!=='true');};
document.querySelectorAll('.export-option').forEach(option=>option.onclick=async event=>{event.stopPropagation();setExportMenuOpen(false);if(!cur)return;try{const path=await API.export_document(cur.id,option.dataset.format);if(path)toast('已导出：'+path.split('/').pop());}catch(error){toast('导出失败：'+error);}});
$('#deleteBtn').onclick=async()=>{
  if(!cur)return;
  const extra=appSettings.delete_audio_with_transcript?'文稿和原始录音都会删除。':'文稿会删除，原始录音会保留。';
  if(!await confirmSettingsAction('删除这份文稿',`文稿会先移入最近删除，可在接下来的几秒内撤销。${extra}`,'删除'))return;
  const result=await API.delete_item(cur.id);
  if(!result||result.ok===false){toast(result&&result.message||'删除失败');return;}
  cur=null; audio.pause(); audio.removeAttribute('src'); $('#content').classList.add('hidden'); $('#empty').classList.remove('hidden');
  await loadHistory(); toast('文稿已移入最近删除',async()=>{const restored=await API.restore_deleted_item(result.id);if(restored&&restored.ok){await loadHistory();toast('文稿已恢复');}else toast(restored&&restored.message||'恢复失败');});
};

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
  if(appSettings.auto_open_import===false) return;
  if(cur && cur.id!==iid && !cur.live) return;   // 用户在看别的记录，不打扰
  clearInterval(startTimer);                      // 收起进度遮罩，改用横幅
  $('#progress').classList.add('hidden');
  $('#empty').classList.add('hidden');
  $('#content').classList.remove('hidden');
  const dur=(st.info&&st.info.duration)||0;
  if(!cur || cur.id!==iid){
    cur={id:iid, title:st.title||'', duration:dur, speakers:{'0':'说话人'}, segments:[], live:true};
    $('#docTitle').textContent=cur.title;
    $('#docEyebrow').textContent='今天 · 本地文稿';
    $('#docStatus').textContent='识别中';
    $('#fileFormat').textContent='音频';
    audio.src='/audio/'+iid; audio.playbackRate=curSpeed;
    $('#curTime').textContent='00:00'; $('#seek').value=0;
    setPlayIcon(false);
    $('#spkBar').innerHTML=''; $('#speakerList').innerHTML='';
  }
  if(dur){ cur.duration=dur; $('#totTime').textContent=fmt(dur); $('#fileDuration').textContent=fmt(dur); }
  $('#docMeta').textContent=`${fmt(cur.duration)} · 识别中…`;
  cur.segments=st.partial;
  renderTranscriptKeepScroll();
  setBanner((st.stage||'识别中…')+'（已出 '+st.partial.length+' 段，可边出边读）');
}

function restoreAfterBackgroundImport(){
  $('#progress').classList.add('hidden');
  if(cur){$('#content').classList.remove('hidden');$('#empty').classList.add('hidden');}
  else{$('#content').classList.add('hidden');$('#empty').classList.remove('hidden');}
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
    const autoOpen=appSettings.auto_open_import!==false;
    const viewing = autoOpen&&(!cur || cur.id===iid);
    if(st.status==='running' && st.partial && st.partial.length){
      renderLive(iid, st);
    }else if(st.status==='draft'){
      if(!draftOpened){
        draftOpened=true;
        clearInterval(startTimer);
        $('#progress').classList.add('hidden');
        await loadHistory();
        if(viewing) await openItem(iid, true);
        else{restoreAfterBackgroundImport();toast('文稿已就绪，正在后台完成处理');}
      }
    }else if(st.status==='done'){
      stopProgress();
      $('#progress').classList.add('hidden');
      await loadHistory();
      if(viewing){ await openItem(iid, true); }
      else restoreAfterBackgroundImport();
      toast('转写完成');
    }else if(st.status==='error'){
      stopProgress();
      $('#progress').classList.add('hidden');
      if(draftOpened && viewing){
        setBanner('说话人分离失败：'+(st.msg||'未知')+'（文稿不受影响）');
      }else if(viewing && !(cur&&cur.live)){
        $('#empty').classList.remove('hidden');
      }else restoreAfterBackgroundImport();
      if(viewing){
        setBanner('转写失败：'+(st.msg||'未知'));
        const retry=document.createElement('button'); retry.type='button'; retry.className='retry-btn'; retry.textContent='重新处理';
        retry.onclick=async()=>{retry.disabled=true;retry.textContent='准备中…';const result=await API.retry_item(iid);if(result&&result.ok){showProgress(st.title||'音频');pollStatus(iid);}else{retry.disabled=false;retry.textContent='重新处理';toast(result&&result.message||'无法重新处理');}};
        $('#liveBanner').append(' ',retry);
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
    if(f.size>2*1024*1024*1024){ toast('文件过大，单个文件不能超过 2GB'); return; }
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

/* ---------- 浏览器预览数据（只在 ?preview=1 时启用） ---------- */
function createPreviewApi(){
  let previewSettings={...DEFAULT_APP_SETTINGS,theme:'solar-bloom',export_directory:'~/Documents'};
  let previewDataPath='~/Library/Application Support/录音转文字/data';
  let items=[
    {id:'demo-1',title:'推广管培生招聘需求会',duration:596,created:'2026-07-15 11:57',n_speakers:2},
    {id:'demo-2',title:'宋英剑背调访谈',duration:232,created:'2026-07-10 10:23',n_speakers:3},
    {id:'demo-3',title:'财务岗位面试',duration:1330,created:'2026-07-08 17:59',n_speakers:2},
    {id:'demo-4',title:'法务岗位复试',duration:3761,created:'2026-07-05 18:52',n_speakers:3},
    {id:'demo-5',title:'一道通背调',duration:520,created:'2026-07-03 20:31',n_speakers:3},
  ];
  const sample={
    id:'demo-1',title:'推广管培生招聘需求会',duration:596,created:'2026-07-15 11:57',audio_format:'M4A',audio_url:'',spk_pending:false,
    speakers:{'0':'林言','1':'陈屿'},
    segments:[
      {spk:0,start:0,text:'我们先对齐一下今天的招聘需求。推广管培生这次更看重候选人的执行力、学习速度，以及跨团队沟通能力。'},
      {spk:1,start:41000,text:'岗位前两个月会轮岗，之后根据表现进入渠道、内容或项目方向。希望候选人能接受快速变化，并且愿意深入一线。'},
      {spk:0,start:86000,text:'面试里可以增加一个真实场景题：给出一周的推广目标，让候选人拆解优先级、资源需求和复盘方式。'},
      {spk:1,start:134000,text:'可以。学历不是唯一判断标准，我们会更关注实习经历中真正做过什么，以及遇到困难时怎么推进。'},
    ],
  };
  return {
    async list_items(){return items;},
    async open_item(id){return {...sample,id,title:(items.find(x=>x.id===id)||items[0]).title};},
    async rename_speaker(id,index,name){sample.speakers[String(index)]=name;return true;},
    async rename_item(id,title){const row=items.find(x=>x.id===id);if(row)row.title=title;return true;},
    async bulk_delete_items(ids){items=items.filter(x=>!ids.includes(x.id));return {ok:true,count:ids.length};},
    async bulk_export_items(ids,format){return {ok:true,count:ids.length,directory:'~/Documents'};},
    async update_segment(id,index,text){sample.segments[index].text=text;return true;},
    async retry_item(){return {ok:true};},
    async retry_diarization(){return {ok:false,message:'预览模式不支持重新分离'}},
    async delete_item(id){items=items.filter(x=>x.id!==id);return true;},
    async restore_deleted_item(){return {ok:false,message:'预览模式不支持恢复'};},
    async clear_history(){const count=items.length;items=[];return {ok:true,count};},
    async clear_model_cache(){return {ok:true,freed:3.1*1024**3};},
    async export_document(id,format){return `/tmp/WordGrab-demo.${format||previewSettings.export_format}`;},
    async export_txt(){return '/tmp/WordGrab-demo.txt';},
    async log(){return true;},
    async pick_file(){return null;},
    async pick_export_directory(){previewSettings.export_directory='~/Documents/WordGrab';return previewSettings.export_directory;},
    async pick_data_directory(){return '~/Documents/WordGrab 数据';},
    async set_data_directory(directory){
      previewDataPath=directory;
      return {ok:true,data_path:directory,moved:true,message:'文稿和录音已移动到新位置'};
    },
    async start_transcribe(){return null;},
    async get_settings(){return {...previewSettings};},
    async update_settings(patch){previewSettings={...previewSettings,...patch};return {...previewSettings};},
    async set_theme(theme){
      previewSettings.theme=theme;try{localStorage.setItem('wordgrab-theme',theme);}catch(_){ }
      return true;
    },
    async get_system_info(){return {version:'1.1.0',data_path:previewDataPath,data_size:184*1024**2,model_path:'~/.cache/modelscope',model_size:2.9*1024**3,model_ready:true,ffmpeg_ok:true,ffmpeg_path:'/opt/homebrew/bin/ffmpeg'};},
    async open_local_resource(){return true;},
  };
}

/* ---------- 启动 ---------- */
async function boot(api,initialId){
  API=api;
  await initSettingsCenter();
  try{
    const firstRun=localStorage.getItem('wordgrab-first-run-seen')!=='1';
    if(firstRun&&API.get_system_info){
      const info=await API.get_system_info();
      if(!info.model_ready||!info.ffmpeg_ok){
        setSettingsOpen(true); showSettingsPage('about');
        await confirmSettingsAction('首次使用准备',
          `${info.ffmpeg_ok?'语音模型将在第一次转写时准备。':'尚未找到 FFmpeg，请先安装后再导入音频。'}${info.model_ready?'':'首次转写需要下载约 2GB 本地模型，过程中可以等待或取消。'}`,
          '知道了');
        setSettingsOpen(false);
      }
      localStorage.setItem('wordgrab-first-run-seen','1');
    }
  }catch(_){ }
  initDrop();
  await loadHistory();
  const startupId=initialId||(appSettings.reopen_last&&appSettings.last_item_id);
  if(startupId){
    try{await openItem(startupId);}catch(_){appSettings.last_item_id='';}
  }
}
if(window.pywebview&&window.pywebview.api) boot(window.pywebview.api);
else if(new URLSearchParams(location.search).get('preview')==='1') boot(createPreviewApi(),'demo-1');
else window.addEventListener('pywebviewready',()=>boot(window.pywebview.api));
