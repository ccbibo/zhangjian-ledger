/* Browser experience driver. No network or financial provider calls. */
let DEMO=null;
const dClone=v=>structuredClone(v),dCents=v=>{if(!/^[+-]?\d+(?:\.\d{1,2})?$/.test(String(v))||Math.abs(Number(v))>1e12)throw Error('金額需為十進位，最多兩位小數且不超過一兆');return Math.round(Number(v)*100)},dMoney=v=>(v/100).toFixed(2),dUUID=()=>crypto.randomUUID(),dDate=s=>/^\d{4}-\d{2}-\d{2}$/.test(s)&&!isNaN(Date.parse(s))&&new Date(s+'T12:00:00Z').toISOString().slice(0,10)===s;
function demoKey(t){return JSON.stringify([t.date,t.name.normalize('NFKC').trim(),dCents(t.amount)])}
function demoReset(){DEMO=dClone(window.LEDGER_DEMO_SEED);if(!DEMO)throw Error('體驗資料尚未載入')}
function dStats(m){const rows=DEMO.ledger.transactions.filter(t=>t.date.startsWith(m)),income=rows.filter(t=>['薪資','股息'].includes(t.category)).reduce((s,t)=>s+dCents(t.amount),0),expense=-rows.filter(t=>['餐飲','交通','生活','保險','娛樂'].includes(t.category)).reduce((s,t)=>s+dCents(t.amount),0);return {month:m,income:dMoney(income),expense:dMoney(expense),balance:dMoney(income-expense),transaction_count:rows.length,excluded_count:rows.filter(t=>['轉帳','投資','信用卡繳款'].includes(t.category)).length}}
function dOverview(m){const ds=DEMO.planning,accounts=ds.items.filter(i=>i.kind==='account'),holdings=ds.items.filter(i=>i.kind==='holding'),loans=ds.items.filter(i=>i.kind==='debt');const av=[...DEMO.ledger.assets,...accounts,...holdings].reduce((s,i)=>s+dCents(i.value),0),dv=[...DEMO.ledger.debts,...loans].reduce((s,i)=>s+dCents(i.balance),0);const spending=Object.fromEntries(['餐飲','交通','生活','保險','娛樂'].map(c=>[c,dMoney(-DEMO.ledger.transactions.filter(t=>t.date.startsWith(m)&&t.category===c).reduce((s,t)=>s+dCents(t.amount),0))]));const allocation=Object.fromEntries(ASSET_CLASSES.map(c=>[c,0]));allocation['現金與存款']=DEMO.ledger.assets.reduce((s,a)=>s+dCents(a.value),0);for(const a of [...accounts,...holdings])allocation[a.asset_class]+=dCents(a.value);let targetValues=null;if(ds.settings.targets){const exact=Object.fromEntries(ASSET_CLASSES.map(c=>[c,av*Number(ds.settings.targets[c]||0)/100]));targetValues=Object.fromEntries(ASSET_CLASSES.map(c=>[c,Math.floor(exact[c])]));const remainder=av-Object.values(targetValues).reduce((a,b)=>a+b,0);ASSET_CLASSES.slice().sort((a,b)=>(exact[b]-targetValues[b])-(exact[a]-targetValues[a])).slice(0,remainder).forEach(c=>targetValues[c]++)}const allocationRows=Object.entries(allocation).filter(([c,v])=>v||ds.settings.targets?.[c]!==undefined).map(([c,v])=>({asset_class:c,value:dMoney(v),actual:av?v/av*100:0,target:String(ds.settings.targets?.[c]||0),difference:targetValues===null?null:dMoney(targetValues[c]-v)}));return {month:m,assets:dMoney(av),liabilities:dMoney(dv),net_worth:dMoney(av-dv),cashflow:dStats(m),spending,budgets:ds.items.filter(i=>i.kind==='budget'&&i.month===m).map(b=>({...b,used:spending[b.category],remaining:dMoney(dCents(b.limit)-dCents(spending[b.category]))})),allocation:allocationRows,investment:{cost:dMoney(holdings.reduce((s,h)=>s+dCents(h.cost),0)),value:dMoney(holdings.reduce((s,h)=>s+dCents(h.value),0)),unrealized:dMoney(holdings.reduce((s,h)=>s+dCents(h.value)-dCents(h.cost),0))},items:dClone(ds.items),settings:dClone(ds.settings),snapshots:dClone(ds.snapshots)}}
function dShift(m,n){let [y,mo]=m.split('-').map(Number),v=y*12+mo-1+n;return Math.floor(v/12)+'-'+String(v%12+1).padStart(2,'0')}
function dHistory(m){const history=Array.from({length:6},(_,i)=>dStats(dShift(m,i-5))),usable=history.slice(0,-1).filter(h=>h.transaction_count),income=usable.length?usable.reduce((s,h)=>s+dCents(h.income),0)/usable.length:0,expense=usable.length?usable.reduce((s,h)=>s+dCents(h.expense),0)/usable.length:0;return {history,forecast:usable.length>=3?[1,2,3].map(i=>({month:dShift(m,i),income:dMoney(Math.round(income)),expense:dMoney(Math.round(expense)),balance:dMoney(Math.round(income)-Math.round(expense))})):[],sample_months:usable.length,assumption:'體驗資料的三個以上已結束月份均值；非對個人未來收支的預測。'}}
function dSave(kind,input){const kinds=['budget','goal','holding','debt','bill','account','investment_flow'];if(!kinds.includes(kind)||!input||typeof input.name!=='string'||!input.name.trim()||input.name.length>80)throw Error('請填寫有效名稱');const old=DEMO.planning.items.find(i=>i.id===input.id);if(input.id&&(!old||old.revision!==input.revision))throw Error('資料已更新，請重新開啟');const value={name:input.name.trim(),kind,id:old?.id||dUUID(),revision:(old?.revision||0)+1};const pos=(v,zero=true)=>{const n=dCents(v);if(n<0||(!zero&&n===0))throw Error('金額須大於或等於零');return dMoney(n)},day=v=>{if(!dDate(v))throw Error('請填寫有效日期');return v};if(kind==='budget'){if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(input.month)||!['餐飲','交通','生活','保險','娛樂'].includes(input.category))throw Error('月份或分類不正確');if(DEMO.planning.items.some(i=>i.id!==old?.id&&i.kind==='budget'&&i.month===input.month&&i.category===input.category))throw Error('此月份與分類已有預算');Object.assign(value,{month:input.month,category:input.category,limit:pos(input.limit,false)})}if(kind==='goal')Object.assign(value,{target:pos(input.target,false),saved:pos(input.saved),deadline:input.deadline?day(input.deadline):''});if(kind==='holding'){const dec=v=>{if(!/^\d+(?:\.\d{1,6})?$/.test(String(v))||Number(v)>1e12)throw Error('請填寫有效數量、價格與匯率');return Number(v)};const q=dec(input.quantity),cost=dec(input.cost_price),price=dec(input.price),fx=input.currency==='TWD'?1:dec(input.fx);if(!q||!fx||!['TWD','USD','HKD'].includes(input.currency)||!ASSET_CLASSES.includes(input.asset_class))throw Error('持倉欄位不正確');Object.assign(value,{symbol:String(input.symbol||'').slice(0,24),asset_class:input.asset_class,currency:input.currency,quantity:String(q),cost_price:String(cost),price:String(price),fx:String(fx),as_of:day(input.as_of),value:pos((Math.round(q*price*fx*100)/100).toFixed(2)),cost:pos((Math.round(q*cost*fx*100)/100).toFixed(2))})}if(kind==='debt'){const rate=Number(input.rate);if(!/^\d+(?:\.\d{1,4})?$/.test(String(input.rate))||rate>100)throw Error('年利率不正確');Object.assign(value,{balance:pos(input.balance),payment:pos(input.payment,false),rate:String(rate),due:day(input.due)})}if(kind==='bill'){if(!['none','monthly','yearly'].includes(input.recurrence)||!['paid','unpaid'].includes(input.status))throw Error('提醒設定不正確');Object.assign(value,{amount:pos(input.amount,false),due:day(input.due),status:input.status,recurrence:input.recurrence});if(DEMO.planning.items.some(i=>i.id!==old?.id&&i.kind==='bill'&&i.name===value.name&&i.due===value.due))throw Error('已有同名同到期日提醒')}if(kind==='account'){if(!ASSET_CLASSES.includes(input.asset_class))throw Error('資產分類不正確');Object.assign(value,{value:pos(input.value),as_of:day(input.as_of),asset_class:input.asset_class})}if(kind==='investment_flow'){const n=dCents(input.amount);if(!n)throw Error('現金流不可為零');Object.assign(value,{amount:dMoney(n),date:day(input.date)})}if(['account','debt'].includes(kind)){const snapshots=kind==='account'?DEMO.ledger.assets:DEMO.ledger.debts;if([...snapshots,...DEMO.planning.items.filter(i=>i.kind===kind&&i.id!==old?.id)].some(i=>i.name.normalize('NFKC').toLowerCase()===value.name.normalize('NFKC').toLowerCase()))throw Error('已有同名帳戶或負債，請勿重複計入')}const saved=dClone(DEMO.planning.items);if(old)DEMO.planning.items[DEMO.planning.items.indexOf(old)]=value;else DEMO.planning.items.push(value);const overview=dOverview(month);if(number(overview.assets)>1e12||number(overview.liabilities)>1e12){DEMO.planning.items=saved;throw Error('合計金額超過安全範圍')}return dClone(value)}
function dXirr(flows){if(flows.length<2||!flows.some(f=>dCents(f.amount)>0)||!flows.some(f=>dCents(f.amount)<0))throw Error('需要投入負數與收回正數');const rows=flows.slice().sort((a,b)=>a.date.localeCompare(b.date)),signs=rows.map(f=>Math.sign(dCents(f.amount)));if(signs.slice(1).filter((s,i)=>s!==signs[i]).length>1)throw Error('現金流多次改變方向，可能沒有唯一XIRR');const base=new Date(rows[0].date),terms=rows.map(f=>[(new Date(f.date)-base)/86400000/365,number(f.amount)]);if(!terms.some(t=>t[0]>0))throw Error('同一天無法年化');const npv=r=>{const ex=terms.map(t=>-t[0]*Math.log1p(r)),scale=Math.max(...ex);return terms.reduce((s,t,i)=>s+t[1]*Math.exp(ex[i]-scale),0)};let a=-.9999,b=1e6;if(npv(a)*npv(b)>0)throw Error('無法確認唯一XIRR');for(let i=0;i<180;i++){const m=(a+b)/2;if(npv(a)*npv(m)<=0)b=m;else a=m}const rate=(a+b)/2;return {rate,percent:Math.round(rate*1e8)/1e6,assumption:'實際天數/365；投入負數，期末估值及收回正數。'}}
function dCsv(text,filename){let rows=[],row=[],cell='',quoted=false,line=1,start=1;for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++}else quoted=!quoted}else if(c===','&&!quoted){row.push(cell);cell=''}else if((c==='\r'||c==='\n')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(cell);if(row.some(v=>v.trim()))rows.push({values:row,line:start});row=[];cell='';line++;start=line}else{cell+=c;if(c==='\n')line++}}if(quoted)throw Error('CSV引號未閉合');row.push(cell);if(row.some(v=>v.trim()))rows.push({values:row,line:start});if(rows.length<2||rows.length>5001)throw Error('CSV需有1–5000筆交易');const headers=rows[0].values.map(h=>h.trim().toLowerCase().replace(/^\uFEFF/,'')),aliases=[['date','日期'],['description','交易摘要','摘要'],['amount','金額'],['category','分類']],indices=aliases.map(a=>{const found=headers.map((h,i)=>a.includes(h)?i:-1).filter(i=>i>=0);if(found.length>1)throw Error('CSV欄位標題重複');return found[0]??-1});if(indices.slice(0,3).some(i=>i<0))throw Error('需要date、description、amount欄位');const keys=new Set(DEMO.ledger.transactions.map(demoKey)),transactions=rows.slice(1).map(r=>{if(r.values.length!==headers.length)throw Error('欄數與標題不一致');const date=r.values[indices[0]].trim(),name=r.values[indices[1]].trim(),amount=dMoney(dCents(r.values[indices[2]].trim())),category=r.values[indices[3]]?.trim()||'生活';if(!dDate(date)||!name||name.length>150||!cats.includes(category))throw Error('日期、摘要或分類不正確');const t={id:dUUID(),date,name,amount,category,source:filename,line:r.line};const key=demoKey(t),duplicate=keys.has(key);keys.add(key);return {...t,keep:!duplicate,duplicate,key}});return {kind:'csv',filename,transactions,assets:[],debts:[],warnings:['體驗模式CSV：只在目前工作區操作；正式持久保存請使用後端。'],metadata:{filename,rows:transactions.length},document_id:dUUID(),status:'pending'}}
async function demoDispatch(url,payload){
 const u=new URL(url,'https://ledger.invalid'),p=u.pathname,m=u.searchParams.get('month')||month;
 if(p==='/api/health')return {ok:true,version:'0.5',storage:'Browser experience',pdf_text:false,image_ocr:false};
 if(p==='/api/state')return dClone(DEMO.ledger);
 if(p==='/api/stats')return dStats(m);
 if(p==='/api/workspace')return dOverview(m);
 if(p==='/api/history')return dHistory(m);
 if(p==='/api/financial-summary')return demoFinancialSummary(u.searchParams.get('period')||'week');
 if(p==='/api/items/save')return dSave(payload.kind,payload.item);
 if(p==='/api/items/delete'){
  const i=DEMO.planning.items.findIndex(i=>i.id===payload.id&&i.revision===payload.revision);
  if(i<0)throw Error('資料已更新');DEMO.planning.items.splice(i,1);return {deleted:true};
 }
 if(p==='/api/settings'){if(payload.news_schedule){const x=payload.news_schedule;if(typeof x.enabled!=='boolean'||!Number.isInteger(x.hour)||!Number.isInteger(x.minute)||x.hour<0||x.hour>23||x.minute<0||x.minute>59)throw Error('每日更新時間不正確')}
  if(payload.theme&&!['light','dark'].includes(payload.theme))throw Error('主題不正確');
  if(payload.widgets&&(!payload.widgets.length||new Set(payload.widgets).size!==payload.widgets.length||payload.widgets.some(w=>!WIDGET_NAMES[w])))throw Error('請至少保留一個有效面板');
  if(payload.targets){let total=0;for(const [k,v]of Object.entries(payload.targets)){if(!ASSET_CLASSES.includes(k)||!/^\d+(?:\.\d{1,2})?$/.test(String(v))||Number(v)>100)throw Error('比例需介於0–100');total+=dCents(v)}if(total!==10000)throw Error('目標比例合計必須為100%')}
  Object.assign(DEMO.planning.settings,dClone(payload));return dClone(DEMO.planning.settings);
 }
 if(p==='/api/budgets/copy'){
  const prev=dShift(payload.month,-1),existing=new Set(DEMO.planning.items.filter(i=>i.kind==='budget'&&i.month===payload.month).map(i=>i.category));
  const source=DEMO.planning.items.filter(i=>i.kind==='budget'&&i.month===prev&&!existing.has(i.category));
  for(const item of source)dSave('budget',{...item,id:undefined,revision:undefined,month:payload.month});return {created:source.length};
 }
 if(p==='/api/snapshot'){
  if(!dDate(payload.day))throw Error('日期不正確');const ov=dOverview(payload.day.slice(0,7));
  DEMO.planning.snapshots=DEMO.planning.snapshots.filter(s=>s.day!==payload.day);
  DEMO.planning.snapshots.push({day:payload.day,assets:ov.assets,liabilities:ov.liabilities,net_worth:ov.net_worth});
  DEMO.planning.snapshots.sort((a,b)=>a.day.localeCompare(b.day));return dClone(DEMO.planning.snapshots);
 }
 if(p==='/api/debt/schedule'){
  const item=DEMO.planning.items.find(i=>i.id===u.searchParams.get('id')&&i.kind==='debt');if(!item)throw Error('找不到貸款');
  let balance=dCents(item.balance),payment=dCents(item.payment),interestTotal=0,rows=[];
  for(let n=1;n<=600&&balance>0;n++){const interest=Math.round(balance*Number(item.rate)/1200);if(payment<=interest)throw Error('還款不足以支付利息');const paid=Math.min(payment,balance+interest),principal=paid-interest;balance-=principal;interestTotal+=interest;rows.push({period:n,payment:dMoney(paid),interest:dMoney(interest),principal:dMoney(principal),remaining:dMoney(balance)})}
  return {rows,interest:dMoney(interestTotal),paid_off:balance===0,assumption:'固定年利率/12，最多600期，不含費用。'};
 }
 if(p==='/api/xirr')return dXirr(payload.flows);
 if(p==='/api/ai/status')return {configured:false,model:'未連接',provider:'none'};
 if(p.startsWith('/api/ai/'))throw Error('體驗模式沒有雲端連線，請先啟動本機後端並設定Key');
 if(p==='/api/news')return {channels:{},job:{status:'idle',message:'體驗工作區未外連新聞服務，不提供假新聞。'},schedule:DEMO.planning.settings.news_schedule||{enabled:true,hour:8,minute:0},next_run:null};if(p==='/api/news/refresh')throw Error('請先啟動後端，才能擷取真實來源與呼叫AI摘要');
 if(p==='/api/demo/reset'){demoReset();return {reset:true}};
 if(p==='/api/backup')return {format:'ledger-browser-experience-v1',data:dClone(DEMO.ledger),planning:dClone(DEMO.planning)};
 if(p==='/api/document'){
  const doc=DEMO.documents[u.searchParams.get('id')];if(!doc)throw Error('找不到來源文件');
  return {filename:doc.filename,kind:doc.kind,metadata:doc.metadata,warnings:doc.warnings,status:'committed'};
 }
 if(p==='/api/upload'){
  if(!payload.filename.toLowerCase().endsWith('.csv'))throw Error('體驗頁只解析CSV；PDF請啟動本機後端，沒有產生OCR結果');
  const text=new TextDecoder().decode(Uint8Array.from(atob(payload.content_base64),c=>c.charCodeAt(0))),doc=dCsv(text,payload.filename);
  DEMO.documents[doc.document_id]=dClone(doc);return doc;
 }
 if(p==='/api/commit'){
  const doc=DEMO.documents[payload.document_id];if(!doc||doc.status==='committed')throw Error('文件不存在或已入帳');
  const keys=new Set(DEMO.ledger.transactions.map(demoKey)),ids=new Set(doc.transactions.map(t=>t.id)),seen=new Set(),selected=[];
  for(const t of payload.transactions.filter(t=>t.keep)){
   if(!ids.has(t.id)||seen.has(t.id)||!dDate(t.date)||!t.name.trim()||t.name.length>150||!cats.includes(t.category))throw Error('覆核欄位不正確');seen.add(t.id);
   const amount=dMoney(dCents(t.amount)),key=demoKey({...t,amount});if(keys.has(key))throw Error('發現重複交易，請取消勾選');keys.add(key);
   const origin=doc.transactions.find(r=>r.id===t.id);selected.push({...t,amount,source:origin.source,line:origin.line,document_id:doc.document_id});
  }
  if(DEMO.ledger.transactions.length+selected.length>10000)throw Error('交易上限10000筆');
  DEMO.ledger.transactions.unshift(...selected);doc.status='committed';
  DEMO.ledger.imports.unshift({document_id:doc.document_id,name:doc.filename,count:selected.length,status:'committed',kind:'csv',warnings:[]});
  return {status:'committed',committed_count:selected.length};
 }
 if(p==='/api/transaction'||p==='/api/transaction/create'){
  const t=payload.transaction,amount=dMoney(dCents(t.amount));if(!dDate(t.date)||!t.name?.trim()||t.name.length>150||!cats.includes(t.category))throw Error('交易欄位不正確');
  const value={date:t.date,name:t.name.trim(),category:t.category,amount},key=demoKey(value);
  if(DEMO.ledger.transactions.some(t=>t.id!==payload.id&&demoKey(t)===key))throw Error('與既有交易重複');
  if(p.endsWith('/create')){const id=dUUID(),document_id=dUUID();DEMO.ledger.transactions.unshift({...value,id,document_id,source:'體驗手動記錄'});DEMO.documents[document_id]={filename:'體驗手動記錄',kind:'manual',metadata:{},warnings:[]}}
  else{const old=DEMO.ledger.transactions.find(t=>t.id===payload.id);if(!old)throw Error('找不到交易');Object.assign(old,value)}
  return {saved:true};
 }
 throw Error('體驗模式尚未提供此操作');
}
async function demoAPI(url,payload){if(!DEMO)demoReset();const before=payload?dClone(DEMO):null;try{const result=await demoDispatch(url,payload);if(payload&&!window.LEDGER_MEMORY_ONLY)localStorage.setItem('ledger-browser-v05',JSON.stringify(DEMO));return result}catch(error){if(before)DEMO=before;throw error}}
