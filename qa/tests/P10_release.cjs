const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {boot}=require('../harness.cjs');
const candidate=process.argv[2]||path.resolve(__dirname,'../baseline/index.html');
const output=process.argv[3]||path.resolve(__dirname,'../reports/P10_round1_raw.json');
const html=fs.readFileSync(candidate,'utf8'),sha=()=>crypto.createHash('sha256').update(fs.readFileSync(candidate)).digest('hex');
const before=sha(),results=[];
function test(id,title,fn){try{const r=fn();results.push({id,title,...r,pass:!!r.pass});}catch(e){results.push({id,title,pass:false,error:e.message});}}
test('P10-01','Unknown initial hash normalizes to dashboard',()=>{const line=html.match(/let view=([^;\n]+)/)?.[1];const b=boot(candidate);const value=b.run(`(()=>{const location={hash:'#missing-page'};return (${line?.split(',review=')[0]||"location.hash.slice(1)||'dashboard'"})})()`);return {pass:value==='dashboard',actual_initial_view:value,evidence:'VM evaluation of exact startup view expression; not browser'};});
test('P10-02','JSON null can start with valid default workspace',()=>{const b=boot(candidate,{stored:'null'});return {pass:b.run('totalAssets()===1800000&&state.demo===true'),actual:b.run('totalAssets()')};});
test('P10-03','Structurally invalid persisted JSON cannot crash dashboard',()=>{const b=boot(candidate,{stored:'{}'});let value,error;try{value=b.run('dashboard()')}catch(e){error=e.message}return {pass:!error&&typeof value==='string',error,messages:b.messages};});
test('P10-04','Malformed saved JSON has visible recovery explanation',()=>{const b=boot(candidate,{stored:'{bad-json'});let warning=b.run("typeof startupWarning !== 'undefined' ? startupWarning : ''");return {pass:!!warning||b.messages.some(m=>/損壞|無效|恢復|復原|備份/.test(m)),warning,messages:b.messages};});
test('P10-05','Transaction summary and source escaped at HTML table output',()=>{const b=boot(candidate);const rendered=b.run(`table([{id:'safe-id',date:'2026-10-01',name:'<img src=x onerror="alert(1)">',category:'生活',source:'<svg onload="alert(1)">',amount:-1}])`);return {pass:!rendered.includes('<img')&&!rendered.includes('<svg')&&rendered.includes('&lt;img'),output:rendered};});
test('P10-06','Initial schema is an acceptable restorable backup',()=>{const b=boot(candidate);return {pass:b.run('validateBackup({format:"ledger-v1",data:structuredClone(initial)})')};});
test('P10-07','PDF OCR, generated AI, news status all clearly disclosed',()=>{const b=boot(candidate);const u=b.run('upload()'),a=b.run('assistant()'),n=b.run('news()');return {pass:/尚未啟用 OCR/.test(u)&&/尚未串接生成式 AI/.test(a)&&/並非今日新聞|不是即時新聞|非即時新聞/.test(n),checks:{ocr:/尚未啟用 OCR/.test(u),ai:/尚未串接生成式 AI/.test(a),news:/並非今日新聞|不是即時新聞|非即時新聞/.test(n)}};});
test('P10-08','Local storage limitations are disclosed',()=>{const b=boot(candidate);let s=b.run('settings()');return {pass:/localStorage/.test(s)&&/不是加密/.test(s)&&/備份/.test(s)};});
test('P10-09','Unknown route via navigation API does not crash',()=>{const b=boot(candidate);b.run("go('unknown-route')");return {pass:b.run("view==='dashboard'&&location.hash==='dashboard'")};});
test('P10-10','Reviewed file unchanged by independent tests',()=>({pass:before===sha(),before,after:sha()}));
const report={reviewer:'P10 Release & integrity',candidate,sha256:before,evidence:'Node VM fixtures, exact-code expression evaluation and static HTML output; no browser/server',passed:results.filter(r=>r.pass).length,total:results.length,results};
fs.writeFileSync(output,JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,total:report.total,failed:results.filter(r=>!r.pass).map(r=>r.id),report:output},null,2));
