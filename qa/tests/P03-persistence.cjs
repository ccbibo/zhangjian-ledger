const {boot}=require('../harness.cjs');
const fs=require('node:fs'),crypto=require('node:crypto'),path=require('node:path');
const file=path.resolve(process.argv[2]||path.join(__dirname,'../baseline/index.html'));
const round=process.argv[3]||'r1';
const cases=[];
function result(id,title,expected,actual,pass,severity='high'){cases.push({id,title,expected,actual,pass,severity,evidence:'Node VM + synthetic DOM/localStorage; no browser'});}
function formDataCtor(){return class {constructor(x){this.x=x}get(k){return this.x[k]}}}
(async()=>{
 let b=boot(file,{failSave:true});
 b.run('parseCSV(sample(),"quota.csv");');b.messages.length=0;
 const before=b.run('JSON.stringify(state)');b.run('commit()');
 result('P03-01','QuotaExceeded during CSV commit','No success message; staged review remains; durable/in-memory state coherent',{messages:b.messages,writes:b.writes.length,reviewCount:b.run('review.length'),transactions:b.run('state.transactions.length')},b.messages.every(m=>!m.includes('已確認'))&&b.run('review.length')===3&&b.run('JSON.stringify(state)')===before,'critical');
 b=boot(file,{failSave:true,FormData:formDataCtor()});
 b.run('editTx("d1")');b.el('#txForm').onsubmit({preventDefault(){},target:{date:'2026-10-28',name:'modified but unsaved',amount:'12345',category:'薪資'}});
 result('P03-02','QuotaExceeded during transaction edit','No saved-success claim; original transaction preserved',{messages:b.messages,transaction:b.run('JSON.stringify(state.transactions[0])')},b.messages.every(m=>!m.includes('已儲存'))&&b.run('state.transactions[0].amount')===68000,'critical');
 b=boot(file,{failSave:true});const resetBefore=b.run('JSON.stringify(state)');b.run('confirmReset(false)');b.el('#doReset').onclick();
 result('P03-03','QuotaExceeded during workspace reset','No updated-success claim; data preserved',{messages:b.messages,demo:b.run('state.demo'),transactionCount:b.run('state.transactions.length')},b.messages.every(m=>!m.includes('已更新'))&&b.run('JSON.stringify(state)')===resetBefore,'critical');
 b=boot(file,{failSave:true});const restoreBefore=b.run('JSON.stringify(state)');const restorePayload=b.run('(()=>{let data=structuredClone(initial);data.transactions=[];data.demo=false;return JSON.stringify({format:"ledger-v1",data})})()');
 await b.el('#backupFile').onchange({target:{files:[{size:restorePayload.length,text:async()=>restorePayload}],value:'restore.json'}});b.el('#confirmRestore').onclick();
 result('P03-04','QuotaExceeded during backup restore','No restored-success claim; original data preserved',{messages:b.messages,demo:b.run('state.demo'),transactionCount:b.run('state.transactions.length')},b.messages.every(m=>!m.includes('已還原'))&&b.run('JSON.stringify(state)')===restoreBefore,'critical');
 let valid=boot(file);valid.run('state.transactions[0].name="persistent custom record";save()');let payload=valid.writes.at(-1)[1];b=boot(file,{stored:payload});
 result('P03-05','Reload valid persisted workspace','Custom record survives',{record:b.run('state.transactions[0].name')},b.run('state.transactions[0].name')==='persistent custom record','medium');
 b=boot(file,{stored:'{broken json'});let corruptionNotice=b.el('#toast').textContent||'',recoveryMarkup=b.el('#main').innerHTML||'',startupWarning=b.run('typeof startupWarning!=="undefined"?startupWarning:""');
 result('P03-06','Corrupt JSON in storage','Provide warning in render path; original storage not overwritten',{demo:b.run('state.demo'),recordCount:b.run('state.transactions.length'),notice:corruptionNotice,startupWarning,recoveryMarkupLength:recoveryMarkup.length},Boolean(corruptionNotice||startupWarning||recoveryMarkup.includes('復原')||recoveryMarkup.includes('損壞'))&&b.writes.length===0,'high');
 b=boot(file,{stored:'{}'});let startupError='';try{b.run('totalAssets()')}catch(e){startupError=e.message}
 result('P03-07','Structurally invalid but valid JSON storage','Validate stored schema and remain operational',{error:startupError},!startupError,'high');
 b=boot(file);b.run('confirmReset(false)');let beforeCancel=b.run('JSON.stringify(state)');b.el('#modal').close();
 result('P03-08','Cancel reset dialog','No state change',{unchanged:b.run('JSON.stringify(state)')===beforeCancel},b.run('JSON.stringify(state)')===beforeCancel,'medium');
 b=boot(file,{failSave:true,FormData:formDataCtor()});b.run('editTx("d1")');let evt={preventDefault(){},target:{date:'2026-10-28',name:'retry custom record',amount:'12345',category:'薪資'}};b.el('#txForm').onsubmit(evt);b.ctx.localStorage.setItem=(k,v)=>b.writes.push([k,v]);b.messages.length=0;b.el('#txForm').onsubmit(evt);
 result('P03-09','Retry same edit dialog after transient save failure','After storage recovers retry saves edited record, not stale snapshot',{messages:b.messages,amount:b.run('state.transactions[0].amount'),name:b.run('state.transactions[0].name'),writtenRecord:b.writes.at(-1)?JSON.parse(b.writes.at(-1)[1]).transactions[0]:null},b.run('state.transactions[0].amount')===12345&&b.run('state.transactions[0].name')==='retry custom record','critical');
 if(round==='r3'){
 b=boot(file,{stored:'{broken financial raw'});let blocked=b.run('state.month="2026-09";save()');
 result('P03-10','Recovery lock blocks ordinary writes','Corrupt original retained; normal save cannot overwrite it',{saveResult:blocked,writes:b.writes.length,locked:b.run('recoveryLocked'),raw:b.run('recoveryRaw'),messages:b.messages},blocked===false&&b.writes.length===0&&b.run('recoveryLocked')&&b.run('recoveryRaw')==='{broken financial raw','critical');
 b.run('globalThis.downloads=[];download=(name,content,type)=>downloads.push({name,content,type})');b.el('#exportRaw').addEventListener=(event,handler)=>b.el('#exportRaw')['on'+event]=handler;b.run('bind()');b.el('#exportRaw').onclick();
 result('P03-11','Recovery original export retains exact bytes as text','Offer original-corrupt content export without replacing or normalizing it',{downloads:b.run('JSON.stringify(downloads)'),writes:b.writes.length},b.run('downloads[0].content')==='{broken financial raw'&&b.writes.length===0,'high');
 b.run('confirmReset(false)');b.el('#doReset').onclick();
 result('P03-12','Explicit recovery reset unlocks only after durable write','Successful explicit reset writes valid blank workspace and clears recovery warning',{writes:b.writes.length,locked:b.run('recoveryLocked'),raw:b.run('recoveryRaw'),warning:b.run('startupWarning'),transactions:b.run('state.transactions.length')},b.writes.length===1&&!b.run('recoveryLocked')&&b.run('recoveryRaw')===''&&b.run('startupWarning')===''&&b.run('state.transactions.length')===0,'high');
 b=boot(file,{stored:'{protected invalid raw',failSave:true});const validRestore=b.run('JSON.stringify({format:"ledger-v1",data:structuredClone(initial)})');await b.el('#backupFile').onchange({target:{files:[{size:validRestore.length,text:async()=>validRestore}],value:'restore.json'}});b.el('#confirmRestore').onclick();
 result('P03-13','Failed explicit recovery restore remains protected','Failed setItem must retain raw, recovery lock and dialog',{writes:b.writes.length,locked:b.run('recoveryLocked'),raw:b.run('recoveryRaw'),dialogOpen:b.el('#modal').open,messages:b.messages},b.writes.length===0&&b.run('recoveryLocked')&&b.run('recoveryRaw')==='{protected invalid raw'&&b.el('#modal').open&&b.messages.every(m=>!m.includes('已還原')),'critical');
 }
 const scores=round==='r1'?{task:18.75,fidelity:15,persistence:5,ui:null,scope:10,errorState:1.25}:round==='r2'?{task:18.75,fidelity:15,persistence:15,ui:null,scope:10,errorState:3.75}:{task:18.75,fidelity:20,persistence:20,ui:null,scope:10,errorState:5};
 const report={role:'P03',focus:'storage failure and consistency',round,source:file,sha256:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),evidenceType:'synthetic Node VM / mocked DOM and localStorage, not UI',cases,passed:cases.filter(c=>c.pass).length,total:cases.length,criticalFailures:cases.filter(c=>!c.pass&&c.severity==='critical').map(c=>c.id),scores,functionalScore:Object.values(scores).reduce((a,b)=>a+(b||0),0),functionalMax:80,verdict:cases.some(c=>!c.pass&&c.severity==='critical')?'FAIL: critical persistence consistency issue; UI untested':'FUNCTIONAL CASES PASS; UI untested'};
 fs.writeFileSync(path.join(__dirname,`../reports/P03-${round}.json`),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
})().catch(e=>{console.error(e);process.exitCode=1});
