const vm=require('node:vm'),fs=require('node:fs');
function boot(file,opts={}){
 const elements=new Map(),messages=[],writes=[];
 function el(s){if(!elements.has(s))elements.set(s,{value:'',innerHTML:'',hidden:false,classList:{add(){},remove(){},toggle(){}},addEventListener(){},querySelector:()=>null,setAttribute(){},querySelectorAll:()=>[],showModal(){this.open=true},close(){this.open=false},focus(){},setSelectionRange(){},click(){},scrollTop:0,scrollHeight:10});return elements.get(s)}
 const ctx=vm.createContext({console,structuredClone,crypto:require('node:crypto').webcrypto,Date,JSON,Number,String,Set,Map,Blob,URL,setTimeout,clearTimeout,FormData:opts.FormData||class {},localStorage:{getItem:()=>opts.stored||null,setItem:(k,v)=>{if(opts.failSave)throw Error('QuotaExceededError');writes.push([k,v])}},location:{hash:''},window:{addEventListener(){},scrollTo(){}},document:{querySelector:el,querySelectorAll:()=>[],body:{classList:{toggle(){}}},createElement:()=>({click(){}})}});
 let source=fs.readFileSync(file,'utf8').split('<script>')[1].split('</script>')[0].replace(/render\(\);\s*$/,'');
 vm.runInContext(source,ctx);vm.runInContext('render=()=>{};toast=t=>{globalThis.messages.push(t)}',Object.assign(ctx,{messages}));
 return {ctx,elements,writes,messages,run:code=>vm.runInContext(code,ctx),el};
}
module.exports={boot};
