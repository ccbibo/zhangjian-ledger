const fs=require('node:fs'),crypto=require('node:crypto'),path=require('node:path');
const {boot}=require('../harness.cjs');
const file=process.argv[2]||path.join(__dirname,'../../dist/index.html');
const out=process.argv[3]||path.join(__dirname,'../reports/P07_r3_offline.json');
const html=fs.readFileSync(file,'utf8'),h=boot(file),modal=h.el('#modal');
const results=[];
const check=(id,pass,evidence,severity='medium')=>results.push({id,pass,evidence,severity:pass?'none':severity,evidenceType:'static-source / Node VM / minimal synthetic DOM fixture'});
let raw='',fields=[],heading=null,attrs={};
Object.defineProperty(modal,'innerHTML',{get:()=>raw,set:v=>{raw=v;attrs={};heading=/<h2[^>]*>/.test(v)?{id:''}:null;fields=[...v.matchAll(/<div class="field">([\s\S]*?)<\/div>/g)].map(m=>{const body=m[1],label=/<label[^>]*>([^<]*)<\/label>/.exec(body),input=/<(?:input|select)\b([^>]*)>/.exec(body);const l=label?{text:label[1],htmlFor:((/<label[^>]*\bfor="([^"]*)"/.exec(body)||[])[1]||'')}:null,n=input?{id:((/\bid="([^"]*)"/.exec(input[1])||[])[1]||''),ariaLabel:((/aria-label="([^"]*)"/.exec(input[1])||[])[1]||'')}:null;return {l,n,querySelector:s=>s==='label'?l:s==='input,select'?n:null}})}});
modal.querySelector=s=>s==='h2'?heading:null;modal.querySelectorAll=s=>s==='.field'?fields:[];modal.setAttribute=(k,v)=>attrs[k]=v;
const formRecords=[];for(const fn of ['editTx','editAssets','editGoal','editDebt']){h.run(fn+'()');formRecords.push({form:fn,count:fields.length,labels:fields.map(f=>({label:f.l?.text,for:f.l?.htmlFor,inputId:f.n?.id})),labelAssociations:fields.every(f=>f.l&&f.n&&f.l.htmlFor===f.n.id&&f.n.id),namedDialog:!!heading?.id&&attrs['aria-labelledby']===heading.id,uniqueIds:new Set(fields.map(f=>f.n?.id)).size===fields.length});}
check('form_label_associations',formRecords.every(r=>r.labelAssociations),formRecords,'high');
check('named_dialog',formRecords.every(r=>r.namedDialog),formRecords.map(r=>({form:r.form,namedDialog:r.namedDialog})));
check('unique_dialog_input_ids',formRecords.every(r=>r.uniqueIds),formRecords.map(r=>({form:r.form,count:r.count,unique:r.uniqueIds})));
check('cash_chart_accessible_values',(h.run('cashbars()').match(/class="bar-pair" role="img" aria-label="[^"<>]+ 收入 [^"<>]+／支出 [^"<>]+"/g)||[]).length===6,h.run('cashbars()'));
function lum(hex){let c=hex.replace('#','').match(/../g).map(x=>parseInt(x,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return c[0]*.2126+c[1]*.7152+c[2]*.0722}
const ratio=(a,b)=>(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
function blend(fg,bg,alpha){const a=fg.slice(1).match(/../g).map(x=>parseInt(x,16)),b=bg.slice(1).match(/../g).map(x=>parseInt(x,16));return '#'+a.map((n,i)=>Math.round(n*alpha+b[i]*(1-alpha)).toString(16).padStart(2,'0')).join('')}
const override=/\.blue,\.red\{color:#fbfbf9\}/.test(html),contrast={blue:ratio('#fbfbf9','#1e50b5'),red:ratio('#fbfbf9','#d8362a')};
check('dark_colored_card_text_contrast',override&&Object.values(contrast).every(n=>n>=4.5),contrast,'high');
const labelContrast={blue:ratio(blend('#fbfbf9','#1e50b5',.8),'#1e50b5'),red:ratio(blend('#fbfbf9','#d8362a',.8),'#d8362a')};
check('colored_card_eyebrow_contrast',html.includes('.tile.red .eyebrow{opacity:1}')||!/\.tile \.eyebrow\{opacity:\.8\}/.test(html)||Object.values(labelContrast).every(n=>n>=4.5),{contrast:labelContrast,selector:'.tile .eyebrow opacity .8',smallNormalTextThreshold:4.5},'medium');
check('HTML_language',/lang="zh-Hant"/.test(html),'lang=zh-Hant');
check('keyboard_focus_style',/:focus-visible/.test(html),'Actual focus behavior untested');
check('upload_keyboard_activation',html.includes("e.key==='Enter'")&&html.includes("e.key===' '"),'Source only; actual key behavior untested');
check('allocation_labels',h.run('allocation()').includes('現金與存款')&&h.run('allocation()').includes('40.0%'),'Allocation text categories and percentages');
check('chart_demo_notice',h.run('chart()').includes('非實際歷史計算'),'Demonstration clearly marked in accessible text');
check('reduced_motion',!/@keyframes|animation:/.test(html),'No animation dependencies found');
check('mobile_nav_semantics',/<nav[^>]*(aria-label|aria-labelledby)/.test(html),'Only one nav landmark; explicit name still absent','low');
check('dialog_overflow_limit',/dialog\{[^}]*max-height:[^}]*overflow:/.test(html),'Authored max-height 90vh and overflow auto present');
const report={tester:'P07',round:3,file,sha256:crypto.createHash('sha256').update(html).digest('hex'),results,scores:{taskResolution:null,dataFidelity:null,statePersistence:null,interactionVisual:null,scopeClarity:null,errorStatePerformance:null},scoreObserved:0,scoreObservedMax:0,uiScore:null,verdict:'PARTIAL STATIC IMPROVEMENT — browser accessibility not verified; no aggregate score'};fs.writeFileSync(out,JSON.stringify(report,null,2));console.log(JSON.stringify({sha256:report.sha256,checks:results.length,passes:results.filter(r=>r.pass).length,failed:results.filter(r=>!r.pass)},null,2));
