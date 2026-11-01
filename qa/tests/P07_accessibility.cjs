const fs=require('node:fs'),crypto=require('node:crypto');
const {boot}=require('../harness.cjs');
const file=process.argv[2]||require('node:path').join(__dirname,'../baseline/index.html');
const out=process.argv[3]||require('node:path').join(__dirname,'../reports/P07_r1.json');
const html=fs.readFileSync(file,'utf8'),h=boot(file);
const results=[];
const check=(id,pass,evidence,severity='medium')=>results.push({id,pass,evidence,severity:pass?'none':severity,kind:'static-source-or-node-VM'});
function lum(hex){let c=hex.replace('#','').match(/../g).map(x=>parseInt(x,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return c[0]*.2126+c[1]*.7152+c[2]*.0722}
const ratio=(a,b)=>(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
check('HTML_language',/lang="zh-Hant"/.test(html),'lang=zh-Hant');
check('keyboard_focus_style',/:focus-visible/.test(html),'Source contains focus-visible outline; actual focus behavior untested');
check('upload_keyboard_activation',html.includes("e.key==='Enter'")&&html.includes("e.key===' '"),'Upload handles Enter and Space');
const formOutputs=[];
for(const fn of ['editTx','editAssets','editGoal','editDebt']){h.run(fn+'()');formOutputs.push([fn,h.el('#modal').innerHTML]);}
const missing=formOutputs.flatMap(([fn,s])=>[...s.matchAll(/<label(?:\s[^>]*)?>([^<]*)<\/label>\s*<(input|select)([^>]*)>/g)].filter(m=>!(/aria-label=/.test(m[3])||/for=/.test(m[0].split('</label>')[0]))).map(m=>({form:fn,label:m[1]})));
check('form_label_associations',missing.length===0,{missing},'high');
check('named_dialog',/<dialog[^>]*(aria-label|aria-labelledby)=/.test(html),'Root dialog lacks aria-label/aria-labelledby in baseline; h2 alone does not name the dialog','medium');
check('cash_chart_accessible_values',/aria-label="[^"]*收入|<caption[^>]*>.*現金流|<table[^>]*(aria-label="[^"]*現金流)/s.test(h.run('dashboard()')),'cashbars uses title attributes and month labels; explicit income/expense values absent from accessible structured chart alternative','medium');
check('chart_demo_notice',h.run('chart()').includes('非實際歷史計算'),'Net-worth chart role img / aria-label marks it as a demonstration');
check('allocation_labels',h.run('allocation()').includes('現金與存款')&&h.run('allocation()').includes('40.0%'),'Allocation text legend contains category names and percentages');
check('reduced_motion',!/@keyframes|animation:/.test(html),'No animation effects found; no reduced-motion dependency');
const blueDark=ratio('#19191b','#1e50b5'),redDark=ratio('#19191b','#d8362a');
const hardcodedWhite=html.includes('.blue{background:var(--blue);color:var(--paper)}')&&!/body\.dark\s+\.blue[^}]*color:\s*(#fff|#fbfbf9|white)/.test(html);
check('dark_colored_card_text_contrast',!hardcodedWhite,{blueTextContrast:blueDark,redTextContrast:redDark,normalTextMinimum:4.5,source:'.blue/.red use var(--paper), dark changes paper to #19191b'},'high');
check('mobile_nav_semantics',/<nav[^>]*(aria-label|aria-labelledby)/.test(html),'nav landmark has no accessible label, although there is one nav only','low');
check('dialog_overflow_limit',/dialog[^}]*max-height:[^}]*(overflow|$)|dialog[^}]*overflow[^}]*max-height:/.test(html),'Dialog width 90%; no authored max-height/overflow rule. Native dialog default may constrain, not browser-verified','low');
const report={tester:'P07',round:1,file,sha256:crypto.createHash('sha256').update(html).digest('hex'),evidenceType:'static source inspection, generated HTML, deterministic color math; no browser',results,scores:{taskResolution:null,dataFidelity:null,statePersistence:null,interactionVisual:null,scopeClarity:null,errorStatePerformance:null},scoreObserved:0,scoreObservedMax:0,uiScore:null,verdict:'UNVERIFIED — static issues reported; not an accessibility conformance audit'};
fs.writeFileSync(out,JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
