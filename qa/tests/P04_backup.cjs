const fs=require('node:fs'),path=require('node:path'),{boot}=require('../harness.cjs');
const target=process.argv[2]||path.join(__dirname,'../baseline/index.html');
const app=boot(target),results=[];
function check(name,fn){try{results.push({name,...fn()})}catch(e){results.push({name,error:e.message})}}
const fresh=()=>app.run('({format:"ledger-v1",data:structuredClone(initial)})');
check('valid backup roundtrip',()=>{app.ctx.fixture=fresh();return{accepted:app.run('validateBackup(fixture)'),expected:true}});
check('null transaction entry rejected without importing',()=>{app.ctx.fixture=fresh();app.ctx.fixture.data.transactions=[null];try{return{accepted:app.run('validateBackup(fixture)'),expected:false}}catch(e){return{accepted:false,validationThrows:e.message,uiHandlerCatches:true,expected:false}}});
check('null debt entry rejected without importing',()=>{app.ctx.fixture=fresh();app.ctx.fixture.data.debts=[null];try{return{accepted:app.run('validateBackup(fixture)'),expected:false}}catch(e){return{accepted:false,validationThrows:e.message,uiHandlerCatches:true,expected:false}}});
check('duplicate transaction IDs rejected',()=>{app.ctx.fixture=fresh();app.ctx.fixture.data.transactions[1].id=app.ctx.fixture.data.transactions[0].id;return{accepted:app.run('validateBackup(fixture)'),expected:false,impact:'find(id) returns first transaction for either source/edit action'}});
check('invalid month rejected',()=>{app.ctx.fixture=fresh();app.ctx.fixture.data.month='2026-13';let accepted=app.run('validateBackup(fixture)');app.run('state=fixture.data');let cashbars;try{cashbars=app.run('cashbars()')}catch(e){cashbars=e.message}return{accepted,expected:false,cashbarsResult:cashbars}});
for(const field of ['page','line'])check('HTML in '+field+' rejected or escaped',()=>{
 app.ctx.fixture=fresh();let t=app.ctx.fixture.data.transactions[0];delete t.page;delete t.line;t[field]='<img src=x onerror="alert(1)">';app.ctx.button={dataset:{source:t.id}};
 let accepted=app.run('validateBackup(fixture)');app.run('state=fixture.data;document.querySelectorAll=s=>s==="[data-source]"?[button]:[];bind();button.onclick()');let html=app.el('#modal').innerHTML;
 return{accepted,rawHtmlInSink:html.includes('<img src=x onerror="alert(1)">'),escapedInSink:html.includes('&lt;img'),expected:'reject invalid numeric metadata or escape on output',evidence:'Node VM captured HTML passed into dialog.innerHTML; actual browser script execution not attempted',sink:html};
});
check('HTML in source escaped',()=>{app.ctx.fixture=fresh();let t=app.ctx.fixture.data.transactions[0];t.source='<img src=x onerror="alert(1)">';app.ctx.button={dataset:{source:t.id}};let accepted=app.run('validateBackup(fixture)');app.run('state=fixture.data;document.querySelectorAll=s=>s==="[data-source]"?[button]:[];bind();button.onclick()');let html=app.el('#modal').innerHTML;return{accepted,rawHtmlInSink:html.includes('<img src=x onerror="alert(1)">'),escapedInSink:html.includes('&lt;img'),expected:'escape on output'}});
for(const val of [0,-1,1.5,100001])check('invalid numeric page '+val+' rejected',()=>{app.ctx.fixture=fresh();app.ctx.fixture.data.transactions[0].page=val;return {accepted:app.run('validateBackup(fixture)'),expected:false}});
check('array date rejected',()=>{app.ctx.fixture=fresh();app.ctx.fixture.data.transactions[0].date=['2026-10-28'];return{accepted:app.run('validateBackup(fixture)'),expected:false}});
check('unsafe fractional cent rejected',()=>{app.ctx.fixture=fresh();app.ctx.fixture.data.transactions=[{id:'tiny',date:'2026-10-28',name:'fraction',source:'backup',amount:0.000001,category:'薪資'}];const accepted=app.run('validateBackup(fixture)');app.run('state=fixture.data');return{accepted,storedAmount:0.000001,computedIncome:app.run('income()'),expected:false}});
check('aggregate overflow rejected',()=>{app.ctx.fixture=fresh();app.ctx.fixture.data.assets.forEach(a=>a.value=1000000000000);return{accepted:app.run('validateBackup(fixture)'),expected:false}});
check('duplicate hidden widget rejected',()=>{app.ctx.fixture=fresh();app.ctx.fixture.data.hidden=['net','net'];return{accepted:app.run('validateBackup(fixture)'),expected:false}});
const output={reviewer:'P04',target,evidenceType:'Node VM synthetic fixtures + captured HTML sinks; no browser',results};
console.log(JSON.stringify(output,null,2));
if(process.env.P04_OUT)fs.writeFileSync(process.env.P04_OUT,JSON.stringify(output,null,2));
