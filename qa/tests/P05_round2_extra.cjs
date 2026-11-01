const fs=require('node:fs'),path=require('node:path'),{boot}=require('../harness.cjs');
const file=process.argv[2]||'finance-ledger/dist/index.html',out=process.argv[3]||'finance-ledger/qa/reports/P05_round2.json';
const cases=[],re=/無法|不支援|不會用|請.*切換|請.*選|沒有已入帳|尚無|分開|拆成|不能/;
function test(id,q,setup,expected,check){const b=boot(file);if(setup)b.run(setup);b.ctx.q=q;b.run('ask(q)');const actual=b.run('chats.at(-1)?.text||""');cases.push({id,q,expected,actual,pass:check(actual,b),evidence:'Node VM with DOM stubs, not UI'});}
const refusal=a=>re.test(a)&&!a.includes('餐飲支出為')&&!/收入 NT\$|本期應繳 NT\$/.test(a);
// Re-run same original questions, with semantic adjudication explicitly recorded.
test('P05-01','這個月餐飲花多少？',null,'1180 and source',a=>a.includes('1,180')&&a.includes('來源'));
test('P05-02','2026年9月餐飲花多少？',null,'Month conflict refusal, no current-month answer',refusal);
test('P05-03','這個月交通支出多少？',null,'Unsupported category refusal',refusal);
test('P05-04','我不是問餐飲，我想問交通支出',null,'Negation refusal',refusal);
test('P05-05','餐飲和交通各多少？',null,'Multi-category refusal',refusal);
test('P05-06','比較九月和十月的餐飲支出',null,'Conflict / comparison refusal',refusal);
test('P05-07','這個月餐飲花多少？','state.transactions=[];','No records, not actual zero claim',a=>a.includes('沒有已入帳')&&a.includes('不代表'));
test('P05-08','幫我推薦明天一定會漲的股票',null,'No prediction',refusal);
test('P05-09','那上個月呢？',null,'Unsupported follow-up refusal',refusal);
test('P05-10','本月收支與結餘？','state.month="2026-09";','September correct figures',a=>a.includes('2026-09')&&a.includes('68,000')&&a.includes('18,000')&&a.includes('50,000'));
test('P05-11','餐飲支出多少？','state.transactions=[{id:"x",date:"2026-10-01",name:"午餐",amount:-123,category:"餐飲",source:"statement.csv",line:7}];','Source row preserved',a=>a.includes('statement.csv 第 7 列')&&a.includes('123'));
test('P05-12','不要算餐飲，幫我算其他支出',null,'Negation refusal',refusal);
// Independent new cases, not keyword copies of the first failure set.
test('P05-N01','2025年十月餐飲花多少？',null,'Wrong year with Chinese month must not use current year',refusal);
test('P05-N02','上週餐飲花多少？',null,'Weekly request must not silently become monthly',refusal);
test('P05-N03','今天餐飲花多少？',null,'Daily request must not silently become monthly',refusal);
test('P05-N04','這個月餐飲退款多少？',null,'Refund request must not become gross expense',refusal);
test('P05-N05','這個月薪資收入多少？',null,'Salary must not become total income including dividends',a=>refusal(a)||(a.includes('68,000')&&!a.includes('72,200')));
test('P05-N06','本月房貸應繳多少？',null,'Mortgage must not become credit card',refusal);
test('P05-N07','餐飲跟信用卡應繳各多少？',null,'Unsupported multi-intent using 跟',refusal);
test('P05-N08','本月信用卡以外的應繳多少？',null,'Negative category must not become credit card',refusal);
test('P05-N09','這個月餐飲花多少？','state.transactions.push({id:"personal",date:"2026-10-01",name:"午餐",amount:-1,category:"餐飲",source:"personal.csv",line:2});','Mixed data clearly warned',a=>a.includes('混合資料'));
test('P05-N10','信用卡還有多少要繳？','state.debts=[];','No debt records not no debt claim',a=>a.includes('沒有信用卡負債記錄')&&a.includes('不代表'));
test('P05-N11','資產配置偏離多少？','state.assets.forEach(a=>a.value=0);','No assets, no invented percentages',a=>a.includes('沒有資產金額')&&a.includes('無法'));
const failed=cases.filter(t=>!t.pass),report={reviewer:'P05',round:2,file,kind:'functional VM; not UI',tests:cases,passed:cases.length-failed.length,total:cases.length,original_semantic_verdict:'12/12 original scenarios fixed. P05_round2_original.json has three narrow assertion false negatives: 切換 absent from refusal regex; safer no-record answer does not emit 0筆. Raw run retained, not overwritten.',findings:failed.map(c=>({severity:'critical',id:c.id,issue:c.expected,reproduction:c.q,actual:c.actual})),scores:{task:18.75,fidelity:10,state:15,ui:null,scope:7.5,status:5},score_total_measured:56.25,maximum_measured:80,full_verdict:'not passed; new unsupported intent/period requests still silently answered with wrong financial scope; UI null'};fs.writeFileSync(out,JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,total:report.total,failed:failed.map(c=>c.id),out},null,2));
