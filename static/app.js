const API = window.location.origin;
const OCP=['ocpappprdclu2','ssocpappprdclu','ocpappprdclu','ocpintprdclu2','ocpintprdclu'];
const CLEV='('+OCP.map(function(c){return 'kube_cluster:'+c;}).join(' OR ')+')';
const PAL=['#1a3acc','#0a9450','#6b4fff','#c47900','#005e99','#c52c27','#3d9970','#b044ff','#d98a00','#1abc9c','#e74c3c','#3498db','#8e44ad','#16a085','#d35400'];
const DAYS={"now-24h":1,"now-2d":2,"now-7d":7,"now-14d":14,"now-30d":30,"now-90d":90}

var CMR_DATA=[];
var CMR_EXTRA=[];
var NEW_MS_REGISTRY=[];
var PRJ=[];
var OTH=[];
var INC=[];
var snowError='';
var snowMeta={ctask_count:0, chg_count:0, items:[]};
var OCP_BIN_CHGS=[]; // change_request assigned to DIG-SOCE-SRE-OCP
var OCP_CHG_F={kind:'all', requestedBy:'', risk:'', ci:'', q:''};

Chart.defaults.color='#4a6070';Chart.defaults.borderColor='#dde5ef';Chart.defaults.font.family='Inter,sans-serif';
var CH={},PG=1,PS=10,activeTab='overview';
var CMAP={app:'ocpappprdclu',ap2:'ocpappprdclu2',sso:'ssocpappprdclu',int:'ocpintprdclu',in2:'ocpintprdclu2'};

// ── CMR FILTER ENGINE ────────────────────────────────────
function todayIST(){return new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Kolkata'});}
function parseDateOnly(s){ if(!s) return ''; return String(s).slice(0,10); }
function inDateRange(dateStr, from, to){
  if(!dateStr) return false;
  var d=parseDateOnly(dateStr);
  if(from && d<from) return false;
  if(to && d>to) return false;
  return true;
}
function toggleCustomDateInputs(){
  var fr=document.getElementById('f-time');
  var wrap=document.getElementById('custom-date-wrap');
  if(!fr||!wrap) return;
  wrap.style.display = fr.value==='custom' ? 'flex' : 'none';
}
function getDateWindow(){
  var fr=F?F.fr:'all';
  if(fr==='custom'){
    return {from:F.from||'', to:F.to||todayIST()};
  }
  if(fr==='all') return {from:'',to:''};
  var days=DAYS[fr]||30;
  var c=new Date();
  c.setDate(c.getDate()-days);
  return {from:c.toLocaleDateString('sv-SE',{timeZone:'Asia/Kolkata'}), to:todayIST()};
}
function getFilteredSnowItems(){
  var items=(snowMeta&&snowMeta.items)||[];
  var w=getDateWindow();
  return items.filter(function(it){
    var d=parseDateOnly(it.start||it.planned_start||'');
    return inDateRange(d,w.from,w.to||todayIST());
  });
}
function getFilteredCMR(){
  var today=todayIST(), fr=F?F.fr:'now-30d';
  var clFilter=F?F.cl:'';

  // Reverse map: full cluster name → short code (app, ap2, sso, int, in2)
  var clCode='';
  if(clFilter){
    for(var k in CMAP){if(CMAP[k]===clFilter){clCode=k;break;}}
  }

  var from='',to='';
  if(fr==='custom'){
    from=(F&&F.from)||'';
    to=(F&&F.to)||today;
  }else if(fr!=='all'){
    var days=DAYS[fr]||30, cutoff=new Date();
    cutoff.setDate(cutoff.getDate()-days);
    from=cutoff.toLocaleDateString('sv-SE',{timeZone:'Asia/Kolkata'});
    to=today;
  }

  return CMR_DATA.filter(function(r){
    if(r.d>today) return false;
    if(clCode && r.c!==clCode) return false;
    if(from && r.d<from) return false;
    if(to && r.d>to) return false;
    return true;
  });
}
function calcDORA(filtered){
  var fr=F?F.fr:'now-30d';
  var days=DAYS[fr]||30;
  if(fr==='custom'){
    var from=(F&&F.from)||todayIST(), to=(F&&F.to)||todayIST();
    var a=new Date(from+'T00:00:00+05:30'), b=new Date(to+'T00:00:00+05:30');
    days=Math.max(1, Math.round((b-a)/86400000)+1);
  }else if(fr==='all'){
    if(filtered.length){
      var dates=filtered.map(function(r){return r.d;}).sort();
      var a2=new Date(dates[0]+'T00:00:00+05:30'), b2=new Date(todayIST()+'T00:00:00+05:30');
      days=Math.max(1, Math.round((b2-a2)/86400000)+1);
    }else days=1;
  }
  var total=filtered.length,perDay=days>0?total/days:0;
  var incs=filtered.filter(function(r){return r.i;}),rbs=filtered.filter(function(r){return r.r;});
  var cfr=total>0?(incs.length/total*100):0;
  var mttrH=incs.length>0?incs.reduce(function(a,r){return a+(r.m||0);},0)/incs.length:0;
  var dfLvl,dfLbl;
  if(total===0){dfLvl='N/A';dfLbl='—';}
  else if(perDay>=1){dfLvl='Elite';dfLbl=perDay.toFixed(1)+'/day';}
  else if(perDay>=1/7){dfLvl='High';dfLbl=(perDay*7).toFixed(1)+'/wk';}
  else if(perDay>=1/30){dfLvl='Medium';dfLbl=(perDay*30).toFixed(1)+'/mo';}
  else{dfLvl='Low';dfLbl=(perDay*30).toFixed(2)+'/mo';}

  // Lead time only from real planned window on extras (no invented formula)
  var ltSum=0, ltCnt=0;
  filtered.forEach(function(r){
    var idx=-1;
    for(var i=0;i<CMR_DATA.length;i++){
      if(CMR_DATA[i].d===r.d&&CMR_DATA[i].p===r.p){idx=i;break;}
    }
    if(idx<0||idx>=CMR_EXTRA.length) return;
    var ex=CMR_EXTRA[idx];
    if(ex&&ex.lt_hours!=null&&!isNaN(ex.lt_hours)&&ex.lt_hours>0){
      ltSum+=ex.lt_hours; ltCnt++;
    }
  });
  var ltH=ltCnt>0?parseFloat((ltSum/ltCnt).toFixed(1)):null;
  var ltLabel=ltH==null?'—':('~'+ltH.toFixed(1)+'h');
  var ltLevel=ltH==null?'N/A':(ltH<=1?'Elite':ltH<=24?'High':ltH<=168?'Medium':'Low');
  var ltDesc=ltH==null?'No lead-time field from ServiceNow yet':'Avg from ServiceNow planned windows · '+ltH.toFixed(1)+'h';

  var cfrLvl=total===0?'N/A':(cfr<=5?'Elite':cfr<=10?'High':cfr<=15?'Medium':'Low');
  var mttrLbl,mttrLvl;
  if(incs.length===0){mttrLbl='—';mttrLvl='N/A';}
  else if(mttrH<=0){mttrLbl='—';mttrLvl='N/A';}
  else if(mttrH<1){mttrLbl=Math.round(mttrH*60)+'m';mttrLvl='Elite';}
  else{mttrLbl='~'+mttrH.toFixed(1)+'h';mttrLvl=mttrH<=1?'Elite':mttrH<=24?'High':mttrH<=168?'Medium':'Low';}
  return{total:total,perDay:perDay,days:days,dfLbl:dfLbl,dfLvl:dfLvl,ltH:ltH,ltLabel:ltLabel,ltLevel:ltLevel,ltDesc:ltDesc,incs:incs,rbs:rbs,incCnt:incs.length,rbCnt:rbs.length,cfr:cfr,cfrLbl:total===0?'—':(cfr.toFixed(1)+'%'),cfrLvl:cfrLvl,mttrH:mttrH,mttrLbl:mttrLbl,mttrLvl:mttrLvl};
}
function buildClusterData(filtered){var counts={};OCP.forEach(function(c){counts[c]=0;});filtered.forEach(function(r){var f=CMAP[r.c];if(f)counts[f]++;});return OCP.map(function(c){return{l:c,c:counts[c]};});}
function buildDateWise(filtered){
  var labels=[],ok=[],fail=[],dm={};
  filtered.forEach(function(r){if(!dm[r.d])dm[r.d]={ok:0,fail:0};if(r.i)dm[r.d].fail++;else dm[r.d].ok++;});
  var w=getDateWindow();
  var from=w.from, to=w.to||todayIST();
  if(!from){
    if(filtered.length) from=filtered.map(function(r){return r.d;}).sort()[0];
    else from=to;
  }
  var start=new Date(from+'T00:00:00+05:30');
  var end=new Date(to+'T00:00:00+05:30');
  for(var d=new Date(start); d<=end; d.setDate(d.getDate()+1)){
    var ds=d.toLocaleDateString('sv-SE',{timeZone:'Asia/Kolkata'});
    labels.push(d.toLocaleDateString('en-IN',{day:'2-digit',month:'short',timeZone:'Asia/Kolkata'}));
    ok.push(dm[ds]?dm[ds].ok:0);
    fail.push(dm[ds]?dm[ds].fail:0);
  }
  return{labels:labels,ok:ok,fail:fail};
}
function buildMonthWise(filtered){
  var MIDX={'01':0,'02':1,'03':2,'04':3,'05':4,'06':5,'07':6},counts=[0,0,0,0,0,0,0],incs=[0,0,0,0,0,0,0];
  filtered.forEach(function(r){var m=r.d.substring(5,7),i=MIDX[m];if(i!==undefined){counts[i]++;if(r.i)incs[i]++;}});
 return{labels:["Jan '26","Feb '26","Mar '26","Apr '26","May '26","Jun '26","Jul '26"],counts:counts,incidents:incs};
}
function dC(id){if(CH[id]){CH[id].destroy();delete CH[id];}}
function mkB(id,labels,data,colors,horiz){
  dC(id);var el=document.getElementById(id);if(!el)return;
  var bg=Array.isArray(colors)?colors:data.map(function(_,i){return PAL[i%PAL.length];});
  CH[id]=new Chart(el,{type:'bar',data:{labels:labels,datasets:[{data:data,backgroundColor:bg,borderRadius:7,borderSkipped:false}]},
    options:{indexAxis:horiz?'y':'x',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},
      scales:{x:{grid:{color:'#edf1f7'},ticks:{color:'#4a6070',font:{size:10}}},y:{beginAtZero:true,grid:{color:'#edf1f7'},ticks:{color:'#4a6070',font:{size:10}}}},
      onClick:function(_,els){if(els.length&&!horiz)openMod('Cluster: '+labels[els[0].index],'<b>CMRs:</b> '+data[els[0].index]);}}});
}
function mkM(id,labels,ds){
  dC(id);var el=document.getElementById(id);if(!el)return;
  CH[id]=new Chart(el,{type:'bar',data:{labels:labels,datasets:ds},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:true,position:'bottom',labels:{boxWidth:10,font:{size:10}}}},
      scales:{x:{grid:{display:false},ticks:{color:'#4a6070',font:{size:10}}},y:{beginAtZero:true,grid:{color:'#edf1f7'},ticks:{color:'#4a6070',font:{size:10}}}}}});
}
function mkTL(id,items,vFn,lFn,onCl){
  var el=document.getElementById(id);if(!el)return;el.innerHTML='';
  var mx=Math.max.apply(null,items.map(vFn).concat([1]));
  items.forEach(function(item){
    var d=document.createElement('div');d.className='tli';
    d.innerHTML='<div style="flex:1;min-width:0;overflow:hidden"><div class="tln">'+lFn(item)+'</div><div class="tlb"><div class="tlf" style="width:'+Math.round(vFn(item)/mx*100)+'%"></div></div></div><div class="tlv">'+vFn(item).toLocaleString('en-IN')+'</div>';
    d.addEventListener('click',function(){if(onCl)onCl(item);});el.appendChild(d);
  });
}
var incFilter='all';
function mkInc(id){
  var el=document.getElementById(id);if(!el)return;el.innerHTML='';
  if(!INC.length){
    el.innerHTML='<div style="padding:14px;color:var(--t3);font-size:12px">No incident data from ServiceNow yet.</div>';
    return;
  }
  var incCnt=INC.filter(function(x){return x.t==='inc';}).length;
  var ctCnt=INC.filter(function(x){return x.t==='ctask';}).length;
  // Filter bar
  var fb=document.createElement('div');
  fb.style.cssText='display:flex;gap:6px;margin-bottom:12px;padding:10px 12px;background:linear-gradient(135deg,#f4f7fb,#eef2ff);border-radius:10px;border:1px solid var(--bd2);align-items:center;flex-wrap:wrap';
  fb.innerHTML='<span style="font-size:10px;font-weight:800;color:var(--t3);text-transform:uppercase;letter-spacing:.08em;margin-right:6px">Filter:</span>';
  ['all','inc','ctask'].forEach(function(f){
    var btn=document.createElement('button');
    var lbl=f==='all'?'All Issues ('+INC.length+')':f==='inc'?'\u26D4 INC Logged ('+incCnt+')':'\u26A0 CTASK Logged ('+ctCnt+')';
    btn.textContent=lbl;
    btn.className='pb'+(incFilter===f?' on':'');
    btn.style.cssText='font-size:11px;font-weight:700;padding:5px 14px;border-radius:999px';
    btn.addEventListener('click',function(){incFilter=f;mkInc(id);});
    fb.appendChild(btn);
  });
  // Summary line
  var summ=document.createElement('span');
  summ.style.cssText='margin-left:auto;font-size:10px;color:var(--t3)';
  summ.innerHTML='<strong style="color:var(--er)">'+incCnt+'</strong> INC &middot; <strong style="color:var(--wn)">'+ctCnt+'</strong> CTASK &middot; <strong>'+INC.filter(function(x){return x.rb;}).length+'</strong> Rollbacks';
  fb.appendChild(summ);
  el.appendChild(fb);
  // Filtered list
  var filtered=incFilter==='all'?INC:INC.filter(function(x){return x.t===incFilter;});
  filtered.forEach(function(inc){
    var d=document.createElement('div');d.className='inci';
    var col=inc.t==='inc'?'var(--er)':'var(--wn)';
    var tBadge=inc.t==='inc'
      ?'<span style="font-size:9px;background:#fef1f0;color:var(--er);padding:2px 7px;border-radius:4px;font-weight:700;border:1px solid #f5c6c3;margin-left:6px">INC</span>'
      :'<span style="font-size:9px;background:#fff7e6;color:var(--wn);padding:2px 7px;border-radius:4px;font-weight:700;border:1px solid #f5dba0;margin-left:6px">CTASK</span>';
    d.innerHTML='<div class="idot" style="background:'+col+'"></div><div><div class="ipr">'+inc.p+tBadge+'<span class="ich">'+inc.ch+'</span></div><div class="ids">'+inc.is+'</div><div class="idt">'+inc.dt+(inc.rb?' &middot; <strong>Rollback</strong>':'')+'</div></div>';
    d.addEventListener('click',function(){openMod(inc.p+' \u2014 '+inc.ch,'<b>Type:</b> '+(inc.t==='inc'?'Incident (INC)':'Change Task (CTASK)')+'<br><b>Date:</b> '+inc.dt+'<br><b>Issue:</b> '+inc.is+'<br><b>Rollback:</b> '+(inc.rb?'Yes':'No'));});
    el.appendChild(d);
  });
}
// ── CLOCK ────────────────────────────────────────────────
function tick(){
  var t=new Date().toLocaleString('en-IN',{timeZone:'Asia/Kolkata',hour12:true,weekday:'short',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',second:'2-digit'});
  var cl=document.getElementById('clock');if(cl)cl.textContent=t+' IST';
  var ft=document.getElementById('fts');if(ft)ft.textContent='Updated: '+t+' IST';
}
tick();setInterval(tick,1000);

// ── MODAL ────────────────────────────────────────────────
function openMod(title,body){
  document.getElementById('mtitle').innerHTML=title;
  document.getElementById('mbody').innerHTML='<div style="font-size:13px;line-height:1.7;color:#4a6070">'+body+'</div>';
  document.getElementById('movl').classList.add('on');
}
function closeMod(){document.getElementById('movl').classList.remove('on');}

// ── TABS ─────────────────────────────────────────────────
document.querySelectorAll('.tb').forEach(function(btn){
  btn.addEventListener('click',function(){switchTab(btn.dataset.tab);});
});
function switchTab(name){
  document.querySelectorAll('.tc').forEach(function(t){t.classList.remove('on');});
  document.querySelectorAll('.tb').forEach(function(b){b.classList.remove('on');});
  var panel=document.getElementById('tab-'+name);if(panel)panel.classList.add('on');
  document.querySelectorAll('.tb').forEach(function(b){if(b.dataset.tab===name)b.classList.add('on');});
  activeTab=name;renderTab(name);loadLive();
}
function renderTab(n){
  if(n==='overview')rOV();else if(n==='dora')rDORA();else if(n==='cmr')rCMR();
  else if(n==='projects')rPROJ();else if(n==='pipeline')rPIPE();
}

// ── FILTER STATE ─────────────────────────────────────────
function getF(){
  var cl=document.getElementById('f-cluster').value;
  var fr=document.getElementById('f-time').value;
  var fromEl=document.getElementById('f-from');
  var toEl=document.getElementById('f-to');
  return{
    cl:cl,
    fr:fr,
    from:fromEl?fromEl.value:'',
    to:toEl?toEl.value:'',
    days:DAYS[fr]||30
  };
}
var F=null;

function applyF(){
  F=getF();
  var lu=document.getElementById('lu');if(lu)lu.textContent='Applying...';
  Object.keys(CH).forEach(function(id){if(CH[id]){CH[id].destroy();delete CH[id];}});
  var filtered=getFilteredCMR(),dora=calcDORA(filtered);
  setEl('kv-df',dora.dfLbl);setEl('kl-df',dora.dfLvl);
  setEl('kd-df',filtered.length===0?'No ServiceNow CMRs in this range':filtered.length+' CMRs over '+dora.days+' days');
  setEl('kv-lt',dora.ltLabel);setEl('kl-lt',dora.ltLevel);setEl('kd-lt',dora.ltDesc);
  setEl('kv-cfr', filtered.length===0?'—':dora.cfrLbl);
  setEl('kl-cfr', filtered.length===0?'N/A':dora.cfrLvl);
  setEl('kd-cfr', filtered.length===0?'No ServiceNow CMRs in this range':dora.incCnt+' incidents / '+filtered.length+' CMRs');
  setEl('kv-mttr',filtered.length===0?'—':dora.mttrLbl);
  setEl('kl-mttr',filtered.length===0?'N/A':dora.mttrLvl);
  setEl('kd-mttr',filtered.length===0?'No incidents in this range':'Mean restore time from '+dora.incCnt+' prod incidents');
  var pMap={};filtered.forEach(function(r){pMap[r.p]=1;});
  setEl('sum-cmrs', String(filtered.length));
  setEl('sum-cfr', filtered.length?dora.cfrLbl:'—');
  setEl('sum-proj', String(Object.keys(pMap).length));
  setEl('sum-ctasks', String(snowMeta.ctask_count||0));
  setEl('sum-source', snowSource==='servicenow'?'ServiceNow live':'Waiting for ServiceNow');
  var db=document.getElementById('dbdg');if(db)db.textContent='DORA Metrics';
  var ftr=document.getElementById('ftr-src');
  if(ftr) ftr.textContent = snowSource==='servicenow'
    ? ('Live ServiceNow · '+CMR_DATA.length+' CHGs · '+(snowMeta.ctask_count||0)+' CTASKs · group DIG-SOCE-SRE-OCP')
    : ('ServiceNow: no data yet'+(snowError?' · '+snowError:''));
  renderTab(activeTab);loadLive();
}

// ── HELPERS ──────────────────────────────────────────────
function setEl(id,val){var e=document.getElementById(id);if(e)e.textContent=val;}
function sv(id,v){setEl(id,v);}
function slvl(id,l){setEl(id,l);}

// ── RENDER: OVERVIEW ─────────────────────────────────────
function rOV(){
  var filtered=getFilteredCMR(),dora=calcDORA(filtered),isEmpty=(filtered.length===0);
  setEl('kv-df',isEmpty?'0':dora.dfLbl);setEl('kl-df',isEmpty?'N/A':dora.dfLvl);
  setEl('kd-df',isEmpty?'No ServiceNow CMRs in this range':filtered.length+' CMRs over '+dora.days+' days');
  setEl('kv-lt',isEmpty?'N/A':dora.ltLabel);setEl('kl-lt',isEmpty?'\u2013':dora.ltLevel);
  setEl('kd-lt',isEmpty?'No deployments to measure':dora.ltDesc);
   // ── CFR KPI card — NOW DYNAMIC ──────────────────────────────────
  setEl('kv-cfr', isEmpty ? 'N/A' : dora.cfrLbl);
  setEl('kl-cfr', isEmpty ? 'N/A' : dora.cfrLvl);
  setEl('kd-cfr', isEmpty
    ? 'No CMR deployments in this range'
    : dora.incCnt+' incident'+(dora.incCnt!==1?'s':'')+' / '+filtered.length+' CMRs \xb7 '+
      (F&&F.fr==='all'?'Jan\u2013May 2026':'Last '+dora.days+' days'));

  // ── MTTR KPI card — NOW DYNAMIC ─────────────────────────────────
  setEl('kv-mttr', isEmpty ? 'N/A' : dora.mttrLbl);
  setEl('kl-mttr', isEmpty ? 'N/A' : dora.mttrLvl);
  setEl('kd-mttr', isEmpty
    ? 'No incidents in this range'
    : 'Mean restore time from '+dora.incCnt+' prod incident'+(dora.incCnt!==1?'s':''));

  // ── Header badge ──
  var db=document.getElementById('dbdg');
  if(db)db.textContent='DORA Metrics';
  var db=document.getElementById('dbdg');if(db)db.textContent='DORA Metrics';
  var badge=document.getElementById('df-badge');if(badge)badge.textContent=isEmpty?'No CMRs in range':filtered.length+' CMRs filtered';
  var clData=buildClusterData(filtered);
  mkB('ov-c1',clData.map(function(x){return x.l;}),clData.map(function(x){return x.c;}),'#1a3acc');
  var dw=buildDateWise(filtered);dC('ov-c2');var el2=document.getElementById('ov-c2');
  if(!el2){ renderOcpBinChgs(); return; }
  if(isEmpty){
    var ctx=el2.getContext('2d');el2.width=el2.offsetWidth||600;el2.height=el2.offsetHeight||240;
    ctx.clearRect(0,0,el2.width,el2.height);ctx.fillStyle='#dde5ef';ctx.fillRect(0,0,el2.width,el2.height);
    ctx.fillStyle='#7b90a5';ctx.font='700 14px Inter,sans-serif';ctx.textAlign='center';
    ctx.fillText('No CMR Deployments in Selected Range',el2.width/2,el2.height/2-14);
    ctx.font='12px Inter,sans-serif';
    ctx.fillText('No ServiceNow CMR data for this range',el2.width/2,el2.height/2+14);
    renderOcpBinChgs();
    return;
  }
  CH['ov-c2']=new Chart(el2,{type:'bar',data:{labels:dw.labels,datasets:[
    {label:'Successful',data:dw.ok,  backgroundColor:'#22c55e',borderRadius:4,borderSkipped:false},
    {label:'Incident',  data:dw.fail,backgroundColor:'#ef4444',borderRadius:4,borderSkipped:false}
  ]},options:{responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:true,position:'bottom',labels:{boxWidth:10,font:{size:10}}}},
    scales:{x:{stacked:true,grid:{display:false},ticks:{color:'#4a6070',font:{size:9},maxRotation:45}},
            y:{stacked:true,beginAtZero:true,grid:{color:'#edf1f7'},ticks:{color:'#4a6070',font:{size:10},stepSize:1}}}}});
  renderOcpBinChgs();
}

function getOcpBinByDate(){
  var w=getDateWindow();
  return (OCP_BIN_CHGS||[]).filter(function(ch){
    if(!(w.from||w.to)) return true;
    var start=parseDateOnly(ch.start_date||'');
    var opened=parseDateOnly(ch.opened_at||'');
    // Open/in-progress: keep if opened or start falls in range (planned start can be outside window)
    if(isOcpChgOpen(ch)){
      return inDateRange(opened||start, w.from||null, w.to||null)
        || inDateRange(start||opened, w.from||null, w.to||null);
    }
    var d=start||opened;
    if(!d) return false;
    return inDateRange(d, w.from||null, w.to||null);
  });
}
function ocpChgStateKey(st){
  return String(st||'').trim().toLowerCase();
}
function isOcpChgOpen(ch){
  var st=ocpChgStateKey(ch.state);
  return st && st!=='closed' && st!=='canceled' && st!=='cancelled';
}
function isOcpChgMiddleware(ch){
  return String(ch.middleware_ocp||'').trim().toLowerCase()==='yes';
}
function getFilteredOcpBinChgs(){
  var list=getOcpBinByDate();
  var kind=OCP_CHG_F.kind||'all';
  var req=OCP_CHG_F.requestedBy||'';
  var risk=OCP_CHG_F.risk||'';
  var ci=OCP_CHG_F.ci||'';
  var q=String(OCP_CHG_F.q||'').trim().toLowerCase();
  return list.filter(function(ch){
    var st=ocpChgStateKey(ch.state);
    if(kind==='middleware' && !isOcpChgMiddleware(ch)) return false;
    if(kind==='open' && !isOcpChgOpen(ch)) return false;
    if(kind==='review' && st!=='review') return false;
    if(kind==='closed' && st!=='closed') return false;
    if(kind==='canceled' && st!=='canceled' && st!=='cancelled') return false;
    if(req && String(ch.requested_by||'')!==req) return false;
    if(risk && String(ch.risk||'')!==risk) return false;
    if(ci && String(ch.configuration_item||'')!==ci) return false;
    if(q){
      var blob=[
        ch.number, ch.short_description, ch.configuration_item, ch.state,
        ch.requested_by, ch.opened_by, ch.assigned_to, ch.change_reason,
        ch.outage, ch.middleware_ocp, ch.risk, ch.type, ch.assignment_group
      ].join(' ').toLowerCase();
      if(blob.indexOf(q)<0) return false;
    }
    return true;
  });
}
function fillOcpChgSelect(selId, fieldKey, allLabel, getValue){
  var sel=document.getElementById(selId);
  if(!sel) return;
  var prev=OCP_CHG_F[fieldKey]||sel.value||'';
  var values={};
  getOcpBinByDate().forEach(function(ch){
    var n=String(getValue(ch)||'').trim();
    if(n) values[n]=1;
  });
  var sorted=Object.keys(values).sort(function(a,b){return a.localeCompare(b);});
  sel.innerHTML='<option value="">'+allLabel+' ('+sorted.length+')</option>';
  sorted.forEach(function(n){
    var opt=document.createElement('option');
    opt.value=n; opt.textContent=n;
    sel.appendChild(opt);
  });
  if(prev && values[prev]) sel.value=prev;
  else { sel.value=''; OCP_CHG_F[fieldKey]=''; }
}
function populateOcpChgDropdowns(){
  fillOcpChgSelect('ocpchg-req', 'requestedBy', 'All people', function(ch){ return ch.requested_by; });
  fillOcpChgSelect('ocpchg-risk', 'risk', 'All risk', function(ch){ return ch.risk; });
  fillOcpChgSelect('ocpchg-ci', 'ci', 'All CIs', function(ch){ return ch.configuration_item; });
}
function setOcpChgFilter(key, val){
  if(key==='kind') OCP_CHG_F.kind=val||'all';
  else if(key==='requestedBy') OCP_CHG_F.requestedBy=val||'';
  else if(key==='risk') OCP_CHG_F.risk=val||'';
  else if(key==='ci') OCP_CHG_F.ci=val||'';
  else if(key==='q') OCP_CHG_F.q=val||'';
  var kindEl=document.getElementById('ocpchg-kind');
  if(kindEl && key==='kind') kindEl.value=OCP_CHG_F.kind;
  var reqEl=document.getElementById('ocpchg-req');
  if(reqEl && key==='requestedBy') reqEl.value=OCP_CHG_F.requestedBy;
  var riskEl=document.getElementById('ocpchg-risk');
  if(riskEl && key==='risk') riskEl.value=OCP_CHG_F.risk;
  var ciEl=document.getElementById('ocpchg-ci');
  if(ciEl && key==='ci') ciEl.value=OCP_CHG_F.ci;
  renderOcpBinChgs();
}
function resetOcpChgFilters(){
  OCP_CHG_F={kind:'all', requestedBy:'', risk:'', ci:'', q:''};
  var kindEl=document.getElementById('ocpchg-kind'); if(kindEl) kindEl.value='all';
  var reqEl=document.getElementById('ocpchg-req'); if(reqEl) reqEl.value='';
  var riskEl=document.getElementById('ocpchg-risk'); if(riskEl) riskEl.value='';
  var ciEl=document.getElementById('ocpchg-ci'); if(ciEl) ciEl.value='';
  var qEl=document.getElementById('ocpchg-q'); if(qEl) qEl.value='';
  renderOcpBinChgs();
}
function openOcpBinChgDetail(num){
  var list=OCP_BIN_CHGS||[];
  var ch=null;
  for(var i=0;i<list.length;i++){ if(list[i].number===num){ ch=list[i]; break; } }
  if(!ch){ openMod(num||'CHG','No detail found.'); return; }
  var row=function(k,v){ return '<b>'+escHtml(k)+':</b> '+escHtml(v==null||v===''?'—':v)+'<br>'; };
  var body=''
    +row('Number', ch.number)
    +row('Short description', ch.short_description)
    +row('State', ch.state)+(ch.sub_state?row('Sub-State', ch.sub_state):'')
    +row('Type', ch.type)
    +row('Change Reason', ch.change_reason)
    +row('Risk', ch.risk)
    +row('Priority', ch.priority)
    +row('Category', ch.category)
    +row('Subcategory', ch.subcategory)
    +row('Configuration item', ch.configuration_item)
    +row('Assignment group', ch.assignment_group)
    +row('Assigned to', ch.assigned_to)
    +row('Requested by', ch.requested_by)
    +row('Opened by', ch.opened_by)
    +row('Opened at', ch.opened_at)
    +row('Start date', ch.start_date)
    +row('End date', ch.end_date)
    +row('Conflict status', ch.conflict_status)
    +row('Middleware (OCP Changes)', ch.middleware_ocp)
    +row('Outage', ch.outage)
    +row('Navitaire Impact', ch.navitaire_impact)
    +row('Front End', ch.front_end)
    +row('Database', ch.database)
    +row('Web & Mobile impact', ch.web_mobile_impact)
    +row('Backend change', ch.backend_change)
    +row('Close code', ch.close_code)
    +(ch.description
      ? ('<b>Description:</b><pre style="white-space:pre-wrap;font-size:11px;background:#f4f7fb;padding:10px;border-radius:8px;margin-top:8px;max-height:240px;overflow:auto">'+escHtml(ch.description)+'</pre>')
      : '');
  openMod(ch.number+(ch.short_description?(' · '+String(ch.short_description).slice(0,60)):''), body);
}
function renderOcpBinChgs(){
  populateOcpChgDropdowns();
  var base=getOcpBinByDate();
  var list=getFilteredOcpBinChgs();
  var openN=0, closedN=0, mw=0, reviewN=0;
  base.forEach(function(ch){
    var st=ocpChgStateKey(ch.state);
    if(st==='closed') closedN++;
    else if(isOcpChgOpen(ch)) openN++;
    if(st==='review') reviewN++;
    if(isOcpChgMiddleware(ch)) mw++;
  });
  setEl('ov-ocpchg-total', String(base.length));
  setEl('ov-ocpchg-mw', String(mw));
  setEl('ov-ocpchg-open', String(openN));
  setEl('ov-ocpchg-closed', String(closedN));
  var hint=document.getElementById('ocpchg-hint');
  if(hint){
    hint.textContent='Showing '+list.length+' of '+base.length
      +' in date range · Open/In progress: '+openN
      +(reviewN?' · Review: '+reviewN:'')
      +' · Filter: '+(OCP_CHG_F.kind||'all')
      +(OCP_CHG_F.requestedBy?(' · Requested by: '+OCP_CHG_F.requestedBy):'')
      +(OCP_CHG_F.risk?(' · Risk: '+OCP_CHG_F.risk):'')
      +(OCP_CHG_F.ci?(' · CI: '+OCP_CHG_F.ci):'')
      +(OCP_CHG_F.q?(' · Search: "'+OCP_CHG_F.q+'"'):'')
      +' · Click row for full fields';
  }
  var body=document.getElementById('ov-ocpchg-body');
  if(!body) return;
  if(!list.length){
    body.innerHTML='<div class="ldg">No change requests match the current filters. Try All available time range, or Clear filters.</div>';
    return;
  }
  var h='<div style="overflow-x:auto;max-height:480px;overflow-y:auto"><table class="etbl"><thead><tr>'
    +'<th>#</th><th>CHG</th><th>State</th><th>CI</th><th>Short description</th>'
    +'<th>Middleware OCP</th><th>Requested by</th><th>Opened by</th><th>Assigned to</th>'
    +'<th>Risk</th><th>Type</th><th>Change Reason</th><th>Outage</th><th>Start</th>'
    +'</tr></thead><tbody>';
  list.forEach(function(ch,idx){
    var openBadge=isOcpChgOpen(ch)
      ? '<span style="background:#ffedd5;color:#9a3412;padding:2px 6px;border-radius:4px;font-weight:700">'+escHtml(ch.state||'Open')+'</span>'
      : escHtml(ch.state||'—');
    h+='<tr style="cursor:pointer" onclick="openOcpBinChgDetail('+JSON.stringify(String(ch.number||''))+')">'
      +'<td>'+(idx+1)+'</td>'
      +'<td><span style="font-family:var(--fm);font-size:10px;color:var(--br);font-weight:700">'+escHtml(ch.number)+'</span></td>'
      +'<td style="font-size:11px;font-weight:600">'+openBadge+'</td>'
      +'<td style="font-size:11px;font-weight:600">'+escHtml(ch.configuration_item||'—')+'</td>'
      +'<td style="font-size:11px;max-width:280px">'+escHtml(ch.short_description||'—')+'</td>'
      +'<td style="font-size:11px;text-align:center">'+(isOcpChgMiddleware(ch)
          ?'<span style="background:#dcfce7;color:#166534;padding:2px 6px;border-radius:4px;font-weight:700">Yes</span>'
          :escHtml(ch.middleware_ocp||'—'))+'</td>'
      +'<td style="font-size:11px">'+escHtml(ch.requested_by||'—')+'</td>'
      +'<td style="font-size:11px">'+escHtml(ch.opened_by||'—')+'</td>'
      +'<td style="font-size:11px">'+escHtml(ch.assigned_to||'—')+'</td>'
      +'<td style="font-size:11px">'+escHtml(ch.risk||'—')+'</td>'
      +'<td style="font-size:11px">'+escHtml(ch.type||'—')+'</td>'
      +'<td style="font-size:11px">'+escHtml(ch.change_reason||'—')+'</td>'
      +'<td style="font-size:11px">'+escHtml(ch.outage||'—')+'</td>'
      +'<td style="font-size:10px;white-space:nowrap">'+escHtml((ch.start_date||ch.opened_at||'—').slice(0,16))+'</td>'
      +'</tr>';
  });
  h+='</tbody></table></div>';
  body.innerHTML=h;
}

// ── RENDER: DORA METRICS ─────────────────────────────────
function rDORA(){
  var filtered=getFilteredCMR(),dora=calcDORA(filtered),isEmpty=(filtered.length===0);
  var clData=buildClusterData(filtered);
  mkB('dm-c1',clData.map(function(x){return x.l;}),clData.map(function(x){return x.c;}),clData.map(function(_,i){return PAL[i];}));
  var dw=buildDateWise(filtered);dC('dm-c2');var el2=document.getElementById('dm-c2');
  if(el2){CH['dm-c2']=new Chart(el2,{type:'bar',data:{labels:dw.labels,datasets:[
    {label:'OK',  data:dw.ok,  backgroundColor:'#22c55e',borderRadius:4,borderSkipped:false},
    {label:'Fail',data:dw.fail,backgroundColor:'#ef4444',borderRadius:4,borderSkipped:false}
  ]},options:{responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:true,position:'bottom',labels:{boxWidth:10,font:{size:10}}}},
    scales:{x:{stacked:true,grid:{display:false},ticks:{color:'#4a6070',font:{size:9},maxRotation:45}},
            y:{stacked:true,beginAtZero:true,grid:{color:'#edf1f7'},ticks:{color:'#4a6070',font:{size:10}}}}}});}
  var nsMap={};filtered.forEach(function(r){nsMap[r.p]=(nsMap[r.p]||0)+1;});
  var ns=Object.keys(nsMap).map(function(k){return{l:k,c:nsMap[k]};}).sort(function(a,b){return b.c-a.c;}).slice(0,10);
  mkTL('dm-ns',ns,function(i){return i.c;},function(i){return i.l;},function(item){openMod(item.l,'<b>CMRs in range:</b> '+item.c);});
  mkInc('dm-inc');
  // Lead-time complexity chart only from live lt_hours
  var buckets={'1-2 Services':[],'3-6 Services':[],'7-15 Services':[],'16+ Services':[]};
  filtered.forEach(function(r){
    var idx=-1;for(var i=0;i<CMR_DATA.length;i++){if(CMR_DATA[i].d===r.d&&CMR_DATA[i].p===r.p){idx=i;break;}}
    if(idx<0||idx>=CMR_EXTRA.length)return;
    var ex=CMR_EXTRA[idx]; if(!ex||ex.lt_hours==null)return;
    var tot=(ex.ms||0)+(ex.mf||0)+(ex.nms||0)+(ex.nmf||0);
    var key=tot<=2?'1-2 Services':tot<=6?'3-6 Services':tot<=15?'7-15 Services':'16+ Services';
    buckets[key].push(ex.lt_hours);
  });
  var bLabels=Object.keys(buckets), bVals=bLabels.map(function(k){
    var a=buckets[k]; if(!a.length) return 0;
    return a.reduce(function(s,v){return s+v;},0)/a.length;
  });
  if(bVals.some(function(v){return v>0;})) mkB('dm-c3',bLabels,bVals,'#c47900');
  else { var el=document.getElementById('dm-c3'); if(el){ dC('dm-c3'); var c=el.getContext('2d'); if(c){ el.width=el.offsetWidth||400; el.height=el.offsetHeight||240; c.fillStyle='#7b90a5'; c.font='12px Inter,sans-serif'; c.textAlign='center'; c.fillText('No lead-time data yet', el.width/2, el.height/2);} } }
  var cmrLT=[];
  filtered.forEach(function(r){
    var idx=-1;for(var i=0;i<CMR_DATA.length;i++){if(CMR_DATA[i].d===r.d&&CMR_DATA[i].p===r.p){idx=i;break;}}
    if(idx>=0&&idx<CMR_EXTRA.length){
      var ex=CMR_EXTRA[idx];
      if(ex&&ex.lt_hours!=null&&ex.lt_hours>0){
        var tot=(ex.ms||0)+(ex.mf||0)+(ex.nms||0)+(ex.nmf||0);
        cmrLT.push({n:r.p+(tot?(' ('+tot+' svcs)'):''),v:ex.lt_hours});
      }
    }
  });
  cmrLT.sort(function(a,b){return b.v-a.v;});
  if(cmrLT.length===0){
    var slow=document.getElementById('dm-slow');
    if(slow)slow.innerHTML='<div class="ldg">No lead-time data from ServiceNow yet.</div>';
  }else{
    mkTL('dm-slow',cmrLT.slice(0,8),function(i){return i.v;},function(i){return i.n+' \xb7 '+i.v.toFixed(1)+'h';});
  }
  setEl('cfr-val',   isEmpty ? 'N/A' : dora.cfrLbl);
setEl('cfr-total', isEmpty ? '0'   : filtered.length.toString());
setEl('cfr-issues',isEmpty ? '0'   : dora.incCnt.toString());
setEl('mttr-val', isEmpty ? 'N/A' : dora.mttrLbl);
setEl('mttr-issues',isEmpty ? '0' : dora.rbCnt.toString());
setEl('mttr-lvl', isEmpty ? '\u2013' : dora.mttrLvl);
}

// ── RENDER: CMR TRACKER ──────────────────────────────────
function rCMR(){
  var filtered=getFilteredCMR(),dora=calcDORA(filtered),isEmpty=(filtered.length===0);
  var svs=document.querySelectorAll('#tab-cmr .sgrid .sv');
  if(svs[0])svs[0].textContent=isEmpty?'0':filtered.length.toString();
  if(svs[1]){
    var pMap={};filtered.forEach(function(r){pMap[r.p]=true;});
    svs[1].textContent=isEmpty?'0':Object.keys(pMap).length.toString();
  }
  if(svs[2])svs[2].textContent=isEmpty?'0':dora.incCnt.toString();
  var mw=buildMonthWise(filtered);
  mkM('cmr-c1',mw.labels,[
    {label:'CMRs',      data:mw.counts,    backgroundColor:'#3b5cf5',borderRadius:5},
    {label:'Incidents', data:mw.incidents, backgroundColor:'#f97316',borderRadius:5}
  ]);
  var pMap2={};filtered.forEach(function(r){pMap2[r.p]=(pMap2[r.p]||0)+1;});
  var pList=Object.keys(pMap2).map(function(k){return{n:k,c:pMap2[k]};}).sort(function(a,b){return b.c-a.c;}).slice(0,10);
  if(pList.length===0){
    dC('cmr-c2');var cv=document.getElementById('cmr-c2');
    if(cv){var ctx=cv.getContext('2d');ctx.clearRect(0,0,cv.width,cv.height);ctx.fillStyle='#7b90a5';ctx.font='13px Inter,sans-serif';ctx.textAlign='center';ctx.fillText('No CMRs in selected range',cv.width/2,cv.height/2);}
  }else{mkB('cmr-c2',pList.map(function(x){return x.n;}),pList.map(function(x){return x.c;}),pList.map(function(_,i){return PAL[i];}));}
  mkInc('cmr-inc');
}

// ── RENDER: PROJECTS ─────────────────────────────────────
function rPROJ(){
  var filtered=getFilteredCMR();
  var pMap={};filtered.forEach(function(r){if(!pMap[r.p])pMap[r.p]={n:r.p,c:0,i:false};pMap[r.p].c++;if(r.i)pMap[r.p].i=true;});
  var dynPRJ=Object.keys(pMap).map(function(k){return pMap[k];}).sort(function(a,b){return b.c-a.c;});
  var usePRJ=dynPRJ;
  renderPage(PG,usePRJ);
  var og=document.getElementById('oth-grid');
  if(og){
    og.innerHTML=dynPRJ.length
      ? '<div style="font-size:11px;color:var(--t3);padding:8px">Showing only projects with CMRs from ServiceNow in this range.</div>'
      : '<div style="font-size:11px;color:var(--t3);padding:8px">No projects from ServiceNow yet.</div>';
  }
  dC('proj-chart');var pc=document.getElementById('proj-chart');if(!pc)return;
  CH['proj-chart']=new Chart(pc,{type:'bar',
    data:{labels:usePRJ.map(function(x){return x.n;}),datasets:[{data:usePRJ.map(function(x){return x.c;}),backgroundColor:usePRJ.map(function(_,i){return PAL[i%PAL.length];}),borderRadius:6,borderSkipped:false}]},
    options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},
      scales:{x:{beginAtZero:true,grid:{color:'#edf1f7'},ticks:{color:'#4a6070',font:{size:10}}},y:{grid:{display:false},ticks:{color:'#4a6070',font:{size:10}}}},
      onClick:function(_,els){if(els.length){var r=usePRJ[els[0].index];openMod(r.n,'<b>CMRs in range:</b> '+r.c+'<br><b>Issues:</b> '+(r.i?'Yes':'No'));}}}});
  setTimeout(function(){initCMRDetailWidget();},100);
  setTimeout(function(){initCMRDetailWidget();},100);
  rNewMS();  // ← ADD THIS LINE
}

function renderPage(page,dynPRJ){
  var usePRJ=dynPRJ||[];PG=page;
  var tot=usePRJ.length,pages=Math.ceil(tot/PS),start=(page-1)*PS;
  var tb=document.getElementById('proj-body');if(!tb)return;tb.innerHTML='';
  var totalCMRs=usePRJ.reduce(function(s,r){return s+r.c;},0)||1;
  usePRJ.slice(start,start+PS).forEach(function(row,idx){
    var tr=document.createElement('tr');var sh=((row.c/totalCMRs)*100).toFixed(1);
    tr.innerHTML='<td>'+(start+idx+1)+'</td><td>'+row.n+'</td><td><strong>'+row.c+'</strong></td><td>'+sh+'%</td><td><span class="bdg '+(row.i?'bwn':'bok')+'">'+(row.i?'Issue':'Clean')+'</span></td>';
    tr.addEventListener('click',function(){openMod(row.n,'<b>CMRs:</b> '+row.c+'<br><b>Share:</b> '+sh+'%<br><b>Issues:</b> '+(row.i?'Yes':'No'));});
    tb.appendChild(tr);
  });
  setEl('pg-info','Showing '+(start+1)+'\u2013'+Math.min(start+PS,tot)+' of '+tot);
  var pb2=document.getElementById('pg-badge');if(pb2)pb2.textContent='Page '+page+' of '+Math.max(1,pages);
  var pgb=document.getElementById('pg-btns');if(!pgb)return;pgb.innerHTML='';
  var prev=document.createElement('button');prev.className='pb';prev.innerHTML='&#8249;';prev.disabled=(page===1);prev.onclick=function(){if(PG>1)renderPage(PG-1,usePRJ);};pgb.appendChild(prev);
  for(var i=1;i<=Math.max(1,pages);i++){(function(pi){var btn=document.createElement('button');btn.className='pb'+(i===page?' on':'');btn.textContent=i;btn.onclick=function(){renderPage(pi,usePRJ);};pgb.appendChild(btn);})(i);}
  var next=document.createElement('button');next.className='pb';next.innerHTML='&#8250;';next.disabled=(page>=pages);next.onclick=function(){if(PG<pages)renderPage(PG+1,usePRJ);};pgb.appendChild(next);
}
// ── RENDER: NEW MICROSERVICES REGISTRY ───────────────────
function rNewMS(){
  var rows=[];
  NEW_MS_REGISTRY.forEach(function(entry){
    var cluster=CMAP[entry.cl]||entry.cl;
    (entry.ms||[]).forEach(function(name){
      rows.push({date:entry.d,name:name,type:'MS',project:entry.p,chg:entry.chg||'\u2014',cluster:cluster,sno:entry.sno});
    });
    (entry.mf||[]).forEach(function(name){
      rows.push({date:entry.d,name:name,type:'MF',project:entry.p,chg:entry.chg||'\u2014',cluster:cluster,sno:entry.sno});
    });
  });
  rows.sort(function(a,b){return b.date.localeCompare(a.date);});

  var totalMS=rows.filter(function(r){return r.type==='MS';}).length;
  var totalMF=rows.filter(function(r){return r.type==='MF';}).length;
  var projSet={};rows.forEach(function(r){projSet[r.project]=true;});

  var badge=document.getElementById('new-ms-badge');
  if(badge)badge.textContent=(totalMS+totalMF)+' new services tracked';

  var statsEl=document.getElementById('new-ms-stats');
  if(statsEl){
    statsEl.innerHTML=
      '<div style="flex:1;min-width:120px;background:linear-gradient(160deg,#eef2ff,#fff);border:1px solid #ccd6ff;border-radius:10px;padding:12px;text-align:center"><div style="font-size:24px;font-weight:900;color:#1a3acc">'+totalMS+'</div><div style="font-size:10px;color:var(--t3)">New Microservices</div></div>'+
      '<div style="flex:1;min-width:120px;background:linear-gradient(160deg,#edfaf3,#fff);border:1px solid #c3e8d8;border-radius:10px;padding:12px;text-align:center"><div style="font-size:24px;font-weight:900;color:#0a9450">'+totalMF+'</div><div style="font-size:10px;color:var(--t3)">New Micro-Frontends</div></div>'+
      '<div style="flex:1;min-width:120px;background:linear-gradient(160deg,#f3f0ff,#fff);border:1px solid #d4c8ff;border-radius:10px;padding:12px;text-align:center"><div style="font-size:24px;font-weight:900;color:#6b4fff">'+Object.keys(projSet).length+'</div><div style="font-size:10px;color:var(--t3)">Projects Introduced New Services</div></div>'+
      '<div style="flex:1;min-width:120px;background:linear-gradient(160deg,#fff8e6,#fff);border:1px solid #f5dba0;border-radius:10px;padding:12px;text-align:center"><div style="font-size:24px;font-weight:900;color:#c47900">'+NEW_MS_REGISTRY.length+'</div><div style="font-size:10px;color:var(--t3)">CMRs with New Services</div></div>';
  }

  var el=document.getElementById('new-ms-table');
  if(!el)return;
  if(rows.length===0){el.innerHTML='<div class="ldg">No new microservices deployed.</div>';return;}

  var h='<div style="overflow-x:auto"><table class="etbl"><thead><tr>';
  h+='<th>#</th><th>Deploy Date</th><th>Type</th><th>&#9733; Service Name</th><th>Application / Project</th><th>CMR No.</th><th>Cluster</th>';
  h+='</tr></thead><tbody>';

  rows.forEach(function(r,i){
    var dt=new Date(r.date+'T00:00:00+05:30');
    var dateStr=dt.toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'});
    var typeBadge=r.type==='MS'
      ?'<span style="font-size:9px;background:#edf2ff;color:#1a3acc;padding:3px 8px;border-radius:4px;font-weight:800;border:1px solid #ccd6ff">MS</span>'
      :'<span style="font-size:9px;background:#edfaf3;color:#0a9450;padding:3px 8px;border-radius:4px;font-weight:800;border:1px solid #c3e8d8">MF</span>';
    var starColor=r.type==='MS'?'#f97316':'#a855f7';

    h+='<tr style="cursor:pointer" onclick="openMod(\'&#9733; '+r.name.replace(/'/g,"\\'")+'\',\'';
    h+='<b>Service:</b> '+r.name.replace(/'/g,"\\'")+'<br>';
    h+='<b>Type:</b> '+(r.type==='MS'?'Microservice (Backend)':'Micro-Frontend (UI)')+'<br>';
    h+='<b>Application:</b> '+r.project.replace(/'/g,"\\'")+'<br>';
    h+='<b>CMR:</b> '+r.chg.replace(/'/g,"\\'")+'<br>';
    h+='<b>Deploy Date:</b> '+dateStr+'<br>';
    h+='<b>Cluster:</b> '+r.cluster+'<br>';
    h+='<b>Source:</b> ServiceNow';
    h+='\')">';
    h+='<td>'+(i+1)+'</td>';
    h+='<td style="white-space:nowrap;font-size:11px">'+dateStr+'</td>';
    h+='<td>'+typeBadge+'</td>';
    h+='<td style="font-weight:700;font-family:var(--fm);font-size:11px"><span style="color:'+starColor+';margin-right:4px">&#9733;</span>'+r.name+'</td>';
    h+='<td style="font-weight:600">'+r.project+'</td>';
    h+='<td><span style="font-family:var(--fm);font-size:10px;color:var(--br)">'+r.chg+'</span></td>';
    h+='<td style="font-size:10px;color:var(--t3)">'+r.cluster+'</td>';
    h+='</tr>';
  });
  h+='</tbody></table></div>';

  // Monthly chart
  var monthMap={};
  rows.forEach(function(r){
    var m=r.date.substring(0,7);
    if(!monthMap[m])monthMap[m]={ms:0,mf:0};
    if(r.type==='MS')monthMap[m].ms++;else monthMap[m].mf++;
  });
  var months=Object.keys(monthMap).sort();
  var monthLabels=months.map(function(m){
    var parts=m.split('-');
    var mn=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return mn[parseInt(parts[1])-1]+" '"+parts[0].substring(2);
  });

  el.innerHTML=h;

  dC('new-ms-chart');var cv=document.getElementById('new-ms-chart');if(!cv)return;
  CH['new-ms-chart']=new Chart(cv,{type:'bar',
    data:{labels:monthLabels,datasets:[
      {label:'New MS',data:months.map(function(m){return monthMap[m].ms;}),backgroundColor:'#f97316',borderRadius:5,borderSkipped:false},
      {label:'New MF',data:months.map(function(m){return monthMap[m].mf;}),backgroundColor:'#a855f7',borderRadius:5,borderSkipped:false}
    ]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:true,position:'bottom',labels:{boxWidth:10,font:{size:10}}}},
      scales:{x:{grid:{display:false},ticks:{color:'#4a6070',font:{size:10}}},
              y:{beginAtZero:true,grid:{color:'#edf1f7'},ticks:{color:'#4a6070',font:{size:10},stepSize:1}}}}});
}
// ── RENDER: PIPELINE / CTASK OPS ─────────────────────────
var OPS_RX={
  azkv:/azure\s*key\s*vault|key\s*vault|\bakv\b/i,
  '3scale':/3\s*scale|3scale|threescale/i,
  pipeline:/pipeline|execute\s+pipeline|pipeline\s+run|running/i,
  kafka:/kafka\s*cert|kafka\s*certificate|certificate.*kafka|kafka.*certificate/i
};
var OPS_ACTIVE='ctask';

function opsKw(i){
  return ((i.ctask_short||'')+' '+(i.description||'')+' '+(i.service||'')).toLowerCase();
}
function isOcpOwnedChg(group){
  // CHG raised by / under OCP = parent change_request assignment group is DIG-SOCE-SRE-OCP
  var g=String(group||'').trim();
  return g==='DIG-SOCE-SRE-OCP' || /SRE-OCP/i.test(g);
}
function getOpsBuckets(){
  var items=getFilteredSnowItems();
  var linked=items.filter(function(i){return !!i.chg;});
  var filterRx=function(rx){ return items.filter(function(i){return rx.test(opsKw(i));}); };
  var byChg={};
  linked.forEach(function(i){
    if(!i.chg) return;
    // Only CHGs assigned to OCP (raised by / under OCP) — not parent CHGs owned by other teams
    if(!isOcpOwnedChg(i.chg_assignment_group)) return;
    if(!byChg[i.chg]) byChg[i.chg]={
      chg:i.chg,
      chg_short:i.chg_short||'',
      chg_state:i.chg_state||'',
      chg_assignment_group:i.chg_assignment_group||'',
      service:i.service||'',
      ctasks:[]
    };
    byChg[i.chg].ctasks.push(i);
    if(!byChg[i.chg].service && i.service) byChg[i.chg].service=i.service;
    if(!byChg[i.chg].chg_short && i.chg_short) byChg[i.chg].chg_short=i.chg_short;
    if(!byChg[i.chg].chg_assignment_group && i.chg_assignment_group) byChg[i.chg].chg_assignment_group=i.chg_assignment_group;
  });
  return {
    ctask:items,
    linked:linked,
    ocpchg:Object.keys(byChg).map(function(k){return byChg[k];}).sort(function(a,b){return (b.ctasks.length||0)-(a.ctasks.length||0);}),
    azkv:filterRx(OPS_RX.azkv),
    '3scale':filterRx(OPS_RX['3scale']),
    pipeline:filterRx(OPS_RX.pipeline),
    kafka:filterRx(OPS_RX.kafka)
  };
}
function escHtml(s){
  return String(s==null?'':s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function openCtaskDetail(ctaskNo){
  var items=getFilteredSnowItems();
  var it=null;
  for(var i=0;i<items.length;i++){ if(items[i].ctask===ctaskNo){ it=items[i]; break; } }
  if(!it){ openMod(ctaskNo||'CTASK','No detail found in current filter.'); return; }
  var ms=(it.ms_names||[]).join(', ')||'—';
  var mf=(it.mf_names||[]).join(', ')||'—';
  var body=''
    +'<b>CTASK:</b> '+escHtml(it.ctask)+'<br>'
    +'<b>Parent CHG:</b> '+escHtml(it.chg||'—')+'<br>'
    +'<b>Service / CI:</b> '+escHtml(it.service||'—')+'<br>'
    +'<b>Short description:</b> '+escHtml(it.ctask_short||'—')+'<br>'
    +'<b>Type:</b> '+escHtml(it.ctask_type||'—')+' · <b>State:</b> '+escHtml(it.ctask_state||'—')+'<br>'
    +'<b>Assigned to:</b> '+escHtml(it.assigned_to||'—')+'<br>'
    +'<b>Assignment group:</b> '+escHtml(it.assignment_group||'DIG-SOCE-SRE-OCP')+'<br>'
    +'<b>Planned start:</b> '+escHtml(it.planned_start||'—')+'<br>'
    +'<b>Planned end:</b> '+escHtml(it.planned_end||'—')+'<br>'
    +'<b>Lead time:</b> '+(it.lt_hours!=null?escHtml(it.lt_hours)+'h':'—')+'<br>'
    +'<b>CHG short:</b> '+escHtml(it.chg_short||'—')+'<br>'
    +'<b>CHG state:</b> '+escHtml(it.chg_state||'—')+'<br>'
    +'<b>CHG assignment group:</b> '+escHtml(it.chg_assignment_group||'—')+'<br>'
    +'<b>Microservices:</b> '+escHtml(ms)+'<br>'
    +'<b>Microfrontends:</b> '+escHtml(mf)+'<br>'
    +'<b>Description:</b><pre style="white-space:pre-wrap;font-size:11px;background:#f4f7fb;padding:10px;border-radius:8px;margin-top:8px;max-height:280px;overflow:auto">'+escHtml(it.description||'—')+'</pre>';
  openMod(it.ctask+(it.chg?(' · '+it.chg):''), body);
}
function renderOpsCtaskTable(list, title){
  var body=document.getElementById('ops-detail-body');
  var badge=document.getElementById('ops-detail-badge');
  var ttl=document.getElementById('ops-detail-title');
  if(ttl) ttl.innerHTML=escHtml(title)+' <span class="cb" id="ops-detail-badge">'+(list.length)+' records</span>';
  if(badge) badge.textContent=list.length+' records';
  if(!body) return;
  if(!list.length){ body.innerHTML='<div class="ldg">No matching CTASKs in selected range.</div>'; return; }
  var h='<div style="overflow-x:auto;max-height:520px;overflow-y:auto"><table class="etbl"><thead><tr>'
    +'<th>#</th><th>CTASK</th><th>CHG</th><th>Service</th><th>Short description</th>'
    +'<th>Type</th><th>State</th><th>Assigned to</th><th>Planned start</th><th>Planned end</th>'
    +'</tr></thead><tbody>';
  list.forEach(function(it,idx){
    var ctask=it.ctask||'—';
    h+='<tr style="cursor:pointer" onclick="openCtaskDetail('+JSON.stringify(String(ctask))+')">'
      +'<td>'+(idx+1)+'</td>'
      +'<td><span style="font-family:var(--fm);font-size:10px;color:var(--br);font-weight:700">'+escHtml(ctask)+'</span></td>'
      +'<td><span style="font-family:var(--fm);font-size:10px">'+escHtml(it.chg||'—')+'</span></td>'
      +'<td style="font-weight:600">'+escHtml(it.service||'—')+'</td>'
      +'<td style="font-size:11px;max-width:280px">'+escHtml(it.ctask_short||'—')+'</td>'
      +'<td style="font-size:11px">'+escHtml(it.ctask_type||'—')+'</td>'
      +'<td style="font-size:11px">'+escHtml(it.ctask_state||'—')+'</td>'
      +'<td style="font-size:11px">'+escHtml(it.assigned_to||'—')+'</td>'
      +'<td style="font-size:10px;white-space:nowrap">'+escHtml(it.planned_start||it.start||'—')+'</td>'
      +'<td style="font-size:10px;white-space:nowrap">'+escHtml(it.planned_end||'—')+'</td>'
      +'</tr>';
  });
  h+='</tbody></table></div>'
    +'<div style="font-size:10px;color:var(--t3);margin-top:8px">Click a row for full CTASK detail (description, MS/MF, CHG info).</div>';
  body.innerHTML=h;
}
function renderOpsChgTable(list){
  var body=document.getElementById('ops-detail-body');
  var ttl=document.getElementById('ops-detail-title');
  if(ttl) ttl.innerHTML='CHGs under OCP <span class="cb">'+(list.length)+' CHGs</span>';
  if(!body) return;
  if(!list.length){ body.innerHTML='<div class="ldg">No CHGs assigned to DIG-SOCE-SRE-OCP in selected range.</div>'; return; }
  var h='<div style="overflow-x:auto;max-height:520px;overflow-y:auto"><table class="etbl"><thead><tr>'
    +'<th>#</th><th>CHG</th><th>CHG assignment group</th><th>Service</th><th>CHG short description</th><th>State</th><th>OCP CTASKs</th><th>CTASK numbers</th>'
    +'</tr></thead><tbody>';
  list.forEach(function(row,idx){
    var nums=(row.ctasks||[]).map(function(c){return c.ctask;}).filter(Boolean);
    h+='<tr>'
      +'<td>'+(idx+1)+'</td>'
      +'<td><span style="font-family:var(--fm);font-size:10px;color:var(--br);font-weight:700">'+escHtml(row.chg)+'</span></td>'
      +'<td style="font-size:11px;font-weight:600">'+escHtml(row.chg_assignment_group||'DIG-SOCE-SRE-OCP')+'</td>'
      +'<td style="font-weight:600">'+escHtml(row.service||'—')+'</td>'
      +'<td style="font-size:11px;max-width:320px">'+escHtml(row.chg_short||'—')+'</td>'
      +'<td style="font-size:11px">'+escHtml(row.chg_state||'—')+'</td>'
      +'<td style="text-align:center"><strong>'+nums.length+'</strong></td>'
      +'<td style="font-family:var(--fm);font-size:10px;max-width:360px">'
      +nums.map(function(n){return '<a href="javascript:void(0)" onclick="openCtaskDetail('+JSON.stringify(String(n))+')" style="color:var(--br);margin-right:6px">'+escHtml(n)+'</a>';}).join('')
      +'</td></tr>';
  });
  h+='</tbody></table></div>'
    +'<div style="font-size:10px;color:var(--t3);margin-top:8px">Only CHGs whose assignment group is DIG-SOCE-SRE-OCP. Click a CTASK for full detail.</div>';
  body.innerHTML=h;
}
function showOpsMetric(kind){
  OPS_ACTIVE=kind||'ctask';
  document.querySelectorAll('#tab-pipeline .scard').forEach(function(c){c.classList.remove('active-metric');});
  var map={ctask:'m-ctask',ocpchg:'m-ocpchg',azkv:'m-azkv','3scale':'m-3scale',pipeline:'m-pipeline',kafka:'m-kafka',linked:'m-total-chg-link'};
  var id=map[OPS_ACTIVE];
  if(id){ var el=document.getElementById(id); if(el&&el.parentElement) el.parentElement.classList.add('active-metric'); }
  var buckets=getOpsBuckets();
  var titles={
    ctask:'All CTASKs (OCP bin)',
    ocpchg:'CHGs under OCP (assignment group DIG-SOCE-SRE-OCP)',
    azkv:'Azure Key Vault CTASKs',
    '3scale':'3scale request CTASKs',
    pipeline:'Pipeline running/executing CTASKs',
    kafka:'Kafka certificate CTASKs',
    linked:'CTASKs linked to CHG'
  };
  if(OPS_ACTIVE==='ocpchg') renderOpsChgTable(buckets.ocpchg||[]);
  else renderOpsCtaskTable(buckets[OPS_ACTIVE]||[], titles[OPS_ACTIVE]||'CTASK Detail');
  var panel=document.getElementById('ops-detail-body');
  if(panel) panel.scrollIntoView({behavior:'smooth',block:'nearest'});
}
function rPIPE(){
  var buckets=getOpsBuckets();
  setEl('m-ctask', String((buckets.ctask||[]).length));
  setEl('m-ocpchg', String((buckets.ocpchg||[]).length));
  setEl('m-azkv', String((buckets.azkv||[]).length));
  setEl('m-3scale', String((buckets['3scale']||[]).length));
  setEl('m-pipeline', String((buckets.pipeline||[]).length));
  setEl('m-kafka', String((buckets.kafka||[]).length));
  setEl('m-total-chg-link', String((buckets.linked||[]).length));

  var el=document.getElementById('pipe-empty');
  if(el){
    var w=getDateWindow();
    var label=(F&&F.fr==='custom') ? ((w.from||'start')+' to '+(w.to||'today')) : (F?F.fr:'all');
    el.textContent='Range: '+label+' · Group: DIG-SOCE-SRE-OCP · Click any card for CTASK-level detail';
  }
  showOpsMetric(OPS_ACTIVE||'ctask');
}

// ── CMR DETAIL WIDGET ────────────────────────────────────
function initCMRDetailWidget(){
  var sel=document.getElementById('cmr-date-sel');if(!sel)return;
  var today=todayIST(),dateSet={};
  CMR_DATA.forEach(function(r){if(r.d<=today)dateSet[r.d]=true;});
  var dates=Object.keys(dateSet).sort(function(a,b){return b.localeCompare(a);});
  sel.innerHTML='<option value="">— Choose a Date —</option>';
  dates.forEach(function(d){
    var dt=new Date(d+'T00:00:00+05:30');
    var opt=document.createElement('option');opt.value=d;
    opt.textContent=dt.toLocaleDateString('en-IN',{weekday:'short',day:'2-digit',month:'short',year:'numeric'});
    sel.appendChild(opt);
  });
  if(dates.length>0){sel.value=dates[0];loadCMRDetail(dates[0]);}
}

function loadCMRDetail(dateStr){
  var sumEl=document.getElementById('cmr-detail-summary'),tbl=document.getElementById('cmr-detail-table');
  if(!dateStr){if(tbl)tbl.innerHTML='<div class="ldg">Select a deployment date above.</div>';return;}
  var rows=[];
  CMR_DATA.forEach(function(r,idx){
    if(r.d!==dateStr)return;
    var ex=(CMR_EXTRA&&CMR_EXTRA[idx])||{chg:'',ms:0,mf:0,nms:0,nmf:0};
    var lt=(ex.lt_hours!=null&&ex.lt_hours>0)?('~'+Number(ex.lt_hours).toFixed(1)+'h'):'\u2014';
    rows.push({chg:ex.chg||'\u2014',project:r.p,ms:ex.ms||0,mf:ex.mf||0,nms:ex.nms||0,nmf:ex.nmf||0,
      msn:ex.msn||[],mfn:ex.mfn||[],
      total:(ex.ms||0)+(ex.mf||0)+(ex.nms||0)+(ex.nmf||0),lt:lt,
      cluster:({app:'ocpappprdclu',ap2:'ocpappprdclu2',sso:'ssocpappprdclu',int:'ocpintprdclu',in2:'ocpintprdclu2'})[r.c]||r.c,
      incident:r.i,idx:idx});
  });
  var badge=document.getElementById('detail-badge');
  var dt=new Date(dateStr+'T00:00:00+05:30');
  if(badge)badge.textContent=dt.toLocaleDateString('en-IN',{weekday:'long',day:'2-digit',month:'long',year:'numeric'})+' \xb7 '+rows.length+' CMR'+(rows.length!==1?'s':'');
  var totMS=rows.reduce(function(s,r){return s+r.ms+r.nms;},0),totMF=rows.reduce(function(s,r){return s+r.mf+r.nmf;},0);
  if(sumEl)sumEl.innerHTML='<strong style="color:#1a3acc">'+rows.length+' CMRs</strong> \xb7 <strong style="color:#1a3acc">'+totMS+' MS</strong> <strong style="color:#0a9450">'+totMF+' MF</strong>';
  if(!tbl)return;
  if(rows.length===0){tbl.innerHTML='<div class="ldg">No CMR deployments for this date.</div>';dC('cmr-detail-chart');return;}

  var h='<div style="overflow-x:auto"><table class="etbl"><thead><tr>';
  h+='<th style="width:30px"></th><th>CMR No.</th><th>Project</th>';
  h+='<th style="color:#1a3acc">MS</th><th style="color:#0a9450">MF</th>';
  h+='<th style="color:#f97316">New MS \u2605</th><th style="color:#a855f7">New MF \u2605</th>';
  h+='<th>Total</th><th>Est. Time</th><th>Cluster</th><th>Status</th></tr></thead><tbody>';

  rows.forEach(function(row,ri){
    var hasNames=(row.msn.length>0||row.mfn.length>0);
    var ltColor=row.lt>'~3'?'var(--er)':row.lt>'~2'?'var(--wn)':'var(--ok)';
    var rowId='svc-row-'+ri;

    // Main data row
    h+='<tr style="background:'+(row.incident?'#fff8f0':'')+'" onclick="toggleSvcRow(\''+rowId+'\')">';
    h+='<td><button class="exp-btn" id="exp-'+rowId+'">'+(hasNames?'\u25B6':'\u2022')+'</button></td>';
    h+='<td><span style="font-family:var(--fm);font-size:10px;color:var(--br)">'+row.chg+'</span></td>';
    h+='<td style="font-weight:600">'+row.project+'</td>';
    h+='<td style="text-align:center"><strong style="color:#1a3acc">'+row.ms+'</strong></td>';
    h+='<td style="text-align:center"><strong style="color:#0a9450">'+row.mf+'</strong></td>';
    h+='<td style="text-align:center">'+(row.nms>0?'<strong style="color:#f97316">'+row.nms+'</strong> <span style="font-size:9px;background:#fff0e0;padding:1px 5px;border-radius:4px">NEW</span>':'<span style="color:#ccc">0</span>')+'</td>';
    h+='<td style="text-align:center">'+(row.nmf>0?'<strong style="color:#a855f7">'+row.nmf+'</strong> <span style="font-size:9px;background:#f5f0ff;padding:1px 5px;border-radius:4px">NEW</span>':'<span style="color:#ccc">0</span>')+'</td>';
    h+='<td style="text-align:center"><strong>'+row.total+'</strong></td>';
    h+='<td style="color:'+ltColor+';font-weight:700">'+row.lt+'</td>';
    h+='<td style="font-size:10px;color:var(--t3)">'+row.cluster+'</td>';
    h+='<td>'+(row.incident?'<span style="color:var(--er);font-weight:700">\u26a0 Incident</span>':'<span style="color:var(--ok);font-weight:700">\u2713 Success</span>')+'</td></tr>';

    // Expandable service names row — 4-col card grid
    h+='<tr class="svc-row" id="'+rowId+'"><td colspan="10" class="svc-cell">';
    if(hasNames){
      // Mini stats bar
      var msCount=row.msn.filter(function(n){return!n.endsWith('*');}).length;
      var mfCount=row.mfn.filter(function(n){return!n.endsWith('*');}).length;
      var nmsCount=row.msn.filter(function(n){return n.endsWith('*');}).length;
      var nmfCount=row.mfn.filter(function(n){return n.endsWith('*');}).length;
      h+='<div class="svc-stats">';
      if(msCount>0)h+='<div class="svc-stat"><div class="svc-stat-val" style="color:#1a3acc">'+msCount+'</div><div class="svc-stat-lbl">Microservices</div></div>';
      if(mfCount>0)h+='<div class="svc-stat"><div class="svc-stat-val" style="color:#0a9450">'+mfCount+'</div><div class="svc-stat-lbl">Micro-Frontends</div></div>';
      if(nmsCount>0)h+='<div class="svc-stat"><div class="svc-stat-val" style="color:#f97316">'+nmsCount+'</div><div class="svc-stat-lbl">New MS \u2605</div></div>';
      if(nmfCount>0)h+='<div class="svc-stat"><div class="svc-stat-val" style="color:#a855f7">'+nmfCount+'</div><div class="svc-stat-lbl">New MF \u2605</div></div>';
      h+='<div class="svc-stat"><div class="svc-stat-val" style="color:var(--t1)">'+(row.msn.length+row.mfn.length)+'</div><div class="svc-stat-lbl">Total</div></div>';
      h+='</div>';

      // Render all services in a single 4-col grid
      h+='<div class="svc-grid">';
      row.msn.forEach(function(name,idx){
        var isNew=name.endsWith('*');
        var cn=isNew?name.slice(0,-1):name;
        var cls=isNew?'nms':'ms';
        var typeLabel=isNew?'New Microservice \u2605':'Microservice';
        var iconText=isNew?'\u2605':'MS';
        h+='<div class="svc-card '+cls+'">';
        h+='<div class="svc-card-icon">'+iconText+'</div>';
        h+='<div class="svc-card-info"><div class="svc-card-name">'+cn+'</div><div class="svc-card-type">'+typeLabel+'</div></div>';
        h+='</div>';
      });
      row.mfn.forEach(function(name,idx){
        var isNew=name.endsWith('*');
        var cn=isNew?name.slice(0,-1):name;
        var cls=isNew?'nmf':'mf';
        var typeLabel=isNew?'New Frontend \u2605':'Micro-Frontend';
        var iconText=isNew?'\u2605':'MF';
        h+='<div class="svc-card '+cls+'">';
        h+='<div class="svc-card-icon">'+iconText+'</div>';
        h+='<div class="svc-card-info"><div class="svc-card-name">'+cn+'</div><div class="svc-card-type">'+typeLabel+'</div></div>';
        h+='</div>';
      });
      h+='</div>';
    } else {
      h+='<div class="svc-none">\u2139 Service names not yet recorded. Add msn/mfn arrays to CMR_EXTRA to populate.</div>';
    }
    h+='</td></tr>';
  });

  h+='</tbody></table></div>';tbl.innerHTML=h;

  // Chart
  dC('cmr-detail-chart');var cv=document.getElementById('cmr-detail-chart');if(!cv)return;
  CH['cmr-detail-chart']=new Chart(cv,{type:'bar',
    data:{labels:rows.map(function(r){return r.project.length>18?r.project.substring(0,16)+'\u2026':r.project;}),
      datasets:[
        {label:'MS',    data:rows.map(function(r){return r.ms;}),  backgroundColor:'#3b5cf5',borderRadius:3,borderSkipped:false},
        {label:'MF',    data:rows.map(function(r){return r.mf;}),  backgroundColor:'#22c55e',borderRadius:3,borderSkipped:false},
        {label:'New MS',data:rows.map(function(r){return r.nms;}), backgroundColor:'#f97316',borderRadius:3,borderSkipped:false},
        {label:'New MF',data:rows.map(function(r){return r.nmf;}), backgroundColor:'#a855f7',borderRadius:3,borderSkipped:false}
      ]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:true,position:'bottom',labels:{boxWidth:10,font:{size:10}}}},
      scales:{x:{grid:{display:false},ticks:{color:'#4a6070',font:{size:9},maxRotation:35}},
              y:{beginAtZero:true,grid:{color:'#edf1f7'},ticks:{color:'#4a6070',font:{size:10},stepSize:1}}}}});
}

// Toggle expand/collapse for service name rows
function toggleSvcRow(id){
  var row=document.getElementById(id);
  var btn=document.getElementById('exp-'+id);
  if(!row)return;
  var isOpen=row.classList.contains('open');
  document.querySelectorAll('.svc-row.open').forEach(function(r){
    r.classList.remove('open');
    var b=document.getElementById('exp-'+r.id);
    if(b){b.classList.remove('open');b.innerHTML='\u25B6';}
  });
  if(!isOpen){
    row.classList.add('open');
    if(btn){btn.classList.add('open');btn.innerHTML='\u25BC';}
  }
}

// ── API ──────────────────────────────────────────────────
async function apiGet(path,params){
  var u=new URL(API+path);
  if(params)Object.entries(params).forEach(function(kv){u.searchParams.set(kv[0],kv[1]);});
  var r=await fetch(u);if(!r.ok)throw new Error(await r.text());return r.json();
}

var snowSource='none'; // 'servicenow' | 'none'

async function loadSnowData(){
  var lu=document.getElementById('lu');
  if(lu)lu.textContent='Loading ServiceNow...';
  snowError='';
  CMR_DATA=[]; CMR_EXTRA=[]; NEW_MS_REGISTRY=[]; PRJ=[]; INC=[]; OCP_BIN_CHGS=[];
  try{
    var data=await apiGet('/api/snow/cmr-data',{
      from_date:'2025-01-01',
      assignment_group:'DIG-SOCE-SRE-OCP',
      force_refresh:'false'
    });
    if(data.error){
      snowError=data.error;
      snowSource='none';
    } else {
      if(data.warning) snowError=data.warning;
      CMR_DATA=Array.isArray(data.cmr_data)?data.cmr_data:[];
      CMR_EXTRA=Array.isArray(data.cmr_extra)?data.cmr_extra:[];
      INC=Array.isArray(data.incidents)?data.incidents:[];
      NEW_MS_REGISTRY=Array.isArray(data.new_ms_registry)?data.new_ms_registry:[];
      PRJ=Array.isArray(data.projects)?data.projects:[];
      snowMeta={ctask_count:data.ctask_count||0, chg_count:data.chg_count||0, items:data.items||[]};
      if(data.warning) snowError=data.warning;
      if(data.permission_hint) snowError=data.permission_hint;
      snowSource=CMR_DATA.length?'servicenow':'none';
      console.log('ServiceNow:',CMR_DATA.length,'CMRs', snowMeta.ctask_count,'CTASKs', data.warning||'');
    }
  }catch(e){
    snowError=e.message||String(e);
    snowSource='none';
  }

  try{
    var chgData=await apiGet('/api/snow/ocp-chgs',{
      from_date:'2025-01-01',
      assignment_group:'DIG-SOCE-SRE-OCP',
      force_refresh:'false'
    });
    if(chgData&&!chgData.error){
      OCP_BIN_CHGS=Array.isArray(chgData.items)?chgData.items:[];
      console.log('OCP Bin CHGs:', OCP_BIN_CHGS.length);
    } else if(chgData&&chgData.error){
      console.log('OCP Bin CHGs error:', chgData.error);
    }
  }catch(e){
    console.log('OCP Bin CHGs:', e.message||e);
  }

  return CMR_DATA.length>0 || OCP_BIN_CHGS.length>0;
}

// ── LOAD LIVE — Datadog infra only (no fake numbers) ─────
async function loadLive(){
  var params={from:F.fr};
  if(F.cl) params.cluster=F.cl;

  try{
    var ph=await apiGet('/api/dora/pod-health',params);
    if(ph&&!ph.error&&ph.running!=null){
      setEl('inf-pods',Number(ph.running).toLocaleString('en-IN'));
      setEl('inf-failed',ph.failed!=null?ph.failed:'—');
    }else{
      setEl('inf-pods','—'); setEl('inf-failed','—');
    }
  }catch(e){ setEl('inf-pods','—'); setEl('inf-failed','—'); console.log('Pods:',e.message); }

  try{
    var apm=await apiGet('/api/dora/apm-error-rate',params);
    if(apm&&!apm.error&&apm.error_rate!=null){
      setEl('inf-err',apm.error_rate+'%');
      setEl('inf-p99',(apm.p99_ms!=null?apm.p99_ms:'—')+'ms');
      setEl('inf-rps',apm.requests_per_min!=null?Number(apm.requests_per_min).toLocaleString('en-IN'):'—');
    }else{
      setEl('inf-err','—'); setEl('inf-p99','—'); setEl('inf-rps','—');
    }
  }catch(e){ setEl('inf-err','—'); setEl('inf-p99','—'); setEl('inf-rps','—'); console.log('APM:',e.message); }

  var now=new Date();
  var ts=now.toLocaleString('en-IN',{timeZone:'Asia/Kolkata',hour12:true,
    day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'});
  var eu=document.getElementById('exec-updated');
  if(eu)eu.textContent='As of '+ts+' IST';
  var lu=document.getElementById('lu');
  if(lu){
    var src=snowSource==='servicenow'
      ?('ServiceNow · '+CMR_DATA.length+' CMRs')
      :('No ServiceNow data'+(snowError?' · '+snowError:''));
    lu.textContent=src+' · '+(F.cl?F.cl:'All Clusters')+' · '+F.fr+' · '
      +now.toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata',hour12:true})+' IST';
  }
}

async function refreshAll(){
  await loadSnowData();
  applyF();
  await loadLive();
}

// ── INIT ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',async function(){
  var today=todayIST();
  var fromEl=document.getElementById('f-from');
  var toEl=document.getElementById('f-to');
  if(toEl) toEl.value=today;
  if(fromEl){
    var d=new Date();
    d.setDate(d.getDate()-30);
    fromEl.value=d.toLocaleDateString('sv-SE',{timeZone:'Asia/Kolkata'});
  }
  toggleCustomDateInputs();
  F=getF();
  await loadSnowData();
  applyF();
  setTimeout(function(){loadLive();},600);
});