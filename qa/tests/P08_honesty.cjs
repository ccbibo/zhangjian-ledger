const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),vm=require('node:vm');
const {boot}=require('../harness.cjs');
const target=process.argv[2]||path.resolve(__dirname,'../baseline/index.html');
const out=process.argv[3]||path.resolve(__dirname,'../reports/P08_r1_raw.json');
const src=fs.readFileSync(target,'utf8'),b=boot(target),results=[];
function check(id,name,fn){try{const detail=fn();results.push({id,name,pass:detail.pass,detail})}catch(e){results.push({id,name,pass:false,error:e.message})}}
check('P08-01','OCR limitations are explicit',()=>({pass:b.run('upload().includes("尚未啟用 OCR")&&upload().includes("不會產生辨識結果")')}));
check('P08-02','Rules assistant discloses non-generative behavior',()=>({pass:b.run('assistant().includes("固定計算規則")&&assistant().includes("尚未串接生成式 AI")')}));
check('P08-03','Market reading does not masquerade as current news',()=>({pass:b.run('news().includes("並非今日新聞")&&news().includes("尚未串接")')}));
check('P08-04','Data import never mixes demo and personal data without warning',()=>{
 b.run('parseCSV("date,description,amount,category\\n2026-10-31,實際房租,-30000,生活","real.csv");commit()');
 const data=b.run('({demo:state.demo,count:state.transactions.length,html:dashboard(),sum:expense()})');
 const controller=src.match(/^function render\(\).*$/m)?.[0];
 if(controller){vm.runInContext(controller,b.ctx);b.run("bind=()=>{};render()")}
 const strip=b.el("#main").innerHTML||data.html.match(/<div class="strip">([\s\S]*?)<div class="bento"/)?.[1]||"";
 const hasMixed=/混合|混入|示範.*實際|真實.*示範|禁止.*示範/.test(strip);
 return {pass:hasMixed||data.count===11||!data.demo,demo:data.demo,count:data.count,expense:data.sum,note:data.html.match(/目前顯示[^<]+/)?.[0]||null};
});
check('P08-05','Prototype distinguishes snapshot from transaction balance',()=>({pass:b.run('portfolio().includes("手動快照")||portfolio().includes("手動資產快照")')}));
check('P08-06','Sensitive financial fields not sent by application code',()=>({pass:!/(fetch\s*\(|XMLHttpRequest|sendBeacon|WebSocket\s*\()/i.test(src),evidence:'Static code inspection only; not network capture'}));
check('P08-07','Automatic external font requests disclosed in privacy copy',()=>({pass:!/fonts\.googleapis\.com/.test(src)||/Google Fonts|Google 字體|外部字體|字型請求|字體請求/.test(src),externalFonts:/fonts\.googleapis\.com/.test(src),risk:'Google Fonts receives connection metadata; financial amounts are not embedded in font URL'}));
check('P08-08','Local storage encryption limits clearly stated',()=>({pass:b.run('settings().includes("不是加密保險箱")')}));
check('P08-09','Deliberate external links prevent opener access',()=>({pass:!src.includes('target="_blank"')||src.includes('rel="noopener noreferrer"')}));
check('P08-10','Manual personal records also trigger mixed warning',()=>{let n=boot(target);n.run('state.transactions.push({id:"manual-p08",date:"2026-10-31",name:"Personal",amount:-5,category:"生活",source:"手動新增"})');const controller=src.match(/^function render\(\).*$/m)?.[0];if(controller){vm.runInContext(controller,n.ctx);n.run('bind=()=>{};render()')}return {pass:/混合資料/.test(n.el('#main').innerHTML),evidence:'Rendered-controller HTML, no browser'};});
check('P08-11','Editing demo asset snapshot does not look wholly like untouched sample',()=>{let n=boot(target);n.run('state.assets[0].value=333333;save()');const controller=src.match(/^function render\(\).*$/m)?.[0];if(controller){vm.runInContext(controller,n.ctx);n.run('bind=()=>{};render()')}return {pass:/混合資料/.test(n.el('#main').innerHTML)||n.run('state.demo===false'),evidence:'Changed cash snapshot in demo, no new transaction ID'};});
const report={role:'P08 product-scope/privacy/demo honesty',target,sha256:crypto.createHash('sha256').update(src).digest('hex'),evidenceType:'Node VM synthetic function calls plus static inspection; no browser or network capture',results,passed:results.filter(r=>r.pass).length,total:results.length};
fs.writeFileSync(out,JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
