// Milestone 1: full BRAIN Labs sign-in on host chrome (GPU, DISPLAY:0) until DCV connects.
// Ports labs_functions.open_labs_session to a headed host browser so the WorkSpaces DCV
// pixel-stream can actually render (the headless docker container cannot).
const { chromium } = require('playwright-core');
const fs = require('fs');

const PLATFORM = 'https://platform.worldquantbrain.com';
const BRAINLABS = PLATFORM + '/profile/account/brainlabs';
const creds = fs.readFileSync('/tmp/creds.txt', 'utf8').split('\n');
const EMAIL = (creds[0]||'').trim(), PW = (creds[1]||'').trim();

const LOGF='/tmp/labs_run.log';
try{ fs.writeFileSync(LOGF,''); }catch(e){}
const log = (...a)=>{ const line='[labs] '+a.join(' '); console.log(line); try{ fs.appendFileSync(LOGF, line+'\n'); }catch(e){} };

async function domInfo(page){
  try { return await page.evaluate(()=>JSON.stringify({
    url: location.href.slice(0,70), title: document.title,
    canvas: document.querySelectorAll('canvas').length,
    iframes: document.querySelectorAll('iframe').length,
    buttons: [...document.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(Boolean).slice(0,8),
    body: (document.body?document.body.innerText.replace(/\s+/g,' ').slice(0,180):'')
  })); } catch(e){ return 'domInfo_err:'+e.message; }
}

(async()=>{
  const ctx = await chromium.launchPersistentContext('/tmp/labs_pw', {
    channel: 'chrome', headless: false, viewport: {width:1366, height:850},
    ignoreDefaultArgs: ['--disable-gpu'],
    args: ['--no-first-run','--no-default-browser-check','--disable-gcm-registration','--start-maximized'],
  });
  try{ await ctx.grantPermissions(['clipboard-read','clipboard-write']); log('granted clipboard perms'); }catch(e){ log('grant perms warn', e.message); }
  const page = ctx.pages()[0] || await ctx.newPage();
  const acceptCookies = async (pg)=>{ try{ const b=pg.getByRole('button',{name:/Accept All|^Accept$/i}); if(await b.count()){ await b.first().click({timeout:4000}); log('accepted cookies'); } }catch(e){} };

  log('goto brainlabs');
  await page.goto(BRAINLABS, {waitUntil:'domcontentloaded', timeout:90000}).catch(e=>log('goto warn', e.message));
  await page.waitForTimeout(3500); // let any redirect settle
  await acceptCookies(page);

  // platform sign-in detected by presence of #email
  if (await page.locator('#email').count()) {
    log('platform sign-in form present -> filling');
    await page.locator('#email').fill(EMAIL);
    await page.locator('#password').fill(PW);
    await page.getByRole('button', {name:'Sign In'}).click();
    await page.waitForLoadState('networkidle', {timeout:60000}).catch(()=>{});
    await page.waitForTimeout(2000);
    await page.goto(BRAINLABS, {waitUntil:'domcontentloaded', timeout:90000}).catch(()=>{});
    await page.waitForTimeout(3500);
    await acceptCookies(page);
  }
  log('on', page.url());

  // DIAGNOSTIC: capture brainlabs page structure (post-login)
  await page.screenshot({path:'/tmp/labs_brainlabs.png', fullPage:true}).catch(()=>{});
  const diag = await page.evaluate(()=>JSON.stringify({
    url: location.href,
    inputs: [...document.querySelectorAll('input')].map(i=>({type:i.type,name:i.name,ph:i.placeholder,aria:i.getAttribute('aria-label'),id:i.id})),
    buttons: [...document.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(Boolean),
    h: [...document.querySelectorAll('h1,h2,h3,label')].map(e=>e.innerText.trim()).filter(Boolean).slice(0,25),
    body: (document.body?document.body.innerText.replace(/\s+/g,' ').slice(0,400):'')
  }));
  log('DIAG', diag);

  // labs password + open session (selector may need adjustment per diag)
  let pwBox = page.getByRole('textbox', {name:'Password*'});
  if(!(await pwBox.count())) pwBox = page.locator('input[type=password]').first();
  await pwBox.waitFor({timeout:30000}).catch(e=>log('pwBox not found:', e.message));
  await pwBox.fill(PW);
  log('clicking Sign in to BRAIN Labs');
  const [popup] = await Promise.all([
    ctx.waitForEvent('page', {timeout:90000}),
    page.getByRole('button', {name:'Sign in to BRAIN Labs'}).click(),
  ]);
  await popup.waitForLoadState('domcontentloaded', {timeout:60000}).catch(()=>{});
  // wait for workspaces deeplink url
  for (let i=0;i<60;i++){ if((popup.url()||'').includes('workspaces-web.com')) break; await popup.waitForTimeout(500); }
  log('popup url', (popup.url()||'').slice(0,70));
  await popup.screenshot({path:'/tmp/labs_step_0.png'}).catch(()=>{});
  log('DOM0', await domInfo(popup));

  // accept AWS cookie consent on the workspaces page if present
  await acceptCookies(popup);
  // optional WorkSpaces "Sign in" (often auto-federated, may be absent)
  try {
    const btn = popup.getByRole('button', {name:/^Sign in$/i});
    if (await btn.count()) { await btn.first().click({timeout:8000}); log('clicked WorkSpaces Sign in'); }
  } catch(e){ log('ws signin:', e.message); }

  // dismiss the "Allow permissions" onboarding: click Next while present, then close
  for (let k=0;k<4;k++){
    try{
      const nxt = popup.getByRole('button', {name:/^Next$/i});
      if (await nxt.count()){ await nxt.first().click({timeout:5000}); log('onboarding Next', k); await popup.waitForTimeout(1500); }
      else break;
    }catch(e){ log('next warn', e.message); break; }
  }
  // close any remaining tooltip (Dismiss / Done / X)
  for (const nm of [/^Done$/i,/^Got it$/i,/^Dismiss$/i,/^Close$/i]){
    try{ const b=popup.getByRole('button',{name:nm}); if(await b.count()){ await b.first().click({timeout:3000}); log('closed tip', nm.source); } }catch(e){}
  }

  // poll for DCV connect / Jupyter prep+redirect, dismissing onboarding/consent
  const marks = [10,40,80,120];
  let prev=0;
  for (const t of marks){
    await popup.waitForTimeout((t-prev)*1000); prev=t;
    await acceptCookies(popup);
    try{ const nxt=popup.getByRole('button',{name:/^Next$/i}); if(await nxt.count()){ await nxt.first().click({timeout:3000}); log('onboarding Next @t='+t); } }catch(e){}
    for (const nm of [/^Done$/i,/^Got it$/i,/^Finish$/i]){ try{ const b=popup.getByRole('button',{name:nm}); if(await b.count()){ await b.first().click({timeout:2500}); log('closed tip '+nm.source); } }catch(e){} }
    await popup.screenshot({path:`/tmp/labs_step_${t}.png`}).catch(()=>{});
    log(`t=${t}s`, await domInfo(popup));
  }
  log('READY - entering command loop (drive via /tmp/labs_cmd/*.json)');

  // ---- interactive command loop: lets the operator iteratively drive the DCV canvas ----
  // Commands are JSON files in /tmp/labs_cmd/, processed in lexical order, then renamed .done
  // {action:"click",x,y} {action:"dblclick",x,y} {action:"type",text} {action:"key",keys:"Control+V"}
  // {action:"shot",path} {action:"setclip",text}(writes local clipboard) {action:"wait",ms} {action:"exit"}
  const CMD='/tmp/labs_cmd';
  try{ fs.mkdirSync(CMD,{recursive:true}); }catch(e){}
  const result=(o)=>{ try{ fs.writeFileSync('/tmp/labs_cmd_result.json', JSON.stringify(o)); }catch(e){} };
  result({status:'ready'});
  let running=true; let lastBeat=Date.now(); let hb=0;
  const reconnect=async()=>{
    try{ await acceptCookies(popup); }catch(_){}
    try{ const s=popup.getByRole('button',{name:/^Sign in$/i}); if(await s.count()){ await s.first().click({timeout:8000}); log('HEARTBEAT reconnect: clicked Sign in'); await popup.waitForTimeout(18000); } }catch(_){}
    for(let k=0;k<4;k++){ try{ const n=popup.getByRole('button',{name:/^Next$/i}); if(await n.count()){ await n.first().click({timeout:4000}); await popup.waitForTimeout(1200);} else break; }catch(_){break;} }
    try{ await popup.screenshot({path:'/tmp/labs_reconnected.png'}); }catch(_){}
  };
  while(running){
    let files=[];
    try{ files=fs.readdirSync(CMD).filter(f=>f.endsWith('.json')).sort(); }catch(e){}
    if(!files.length){
      await popup.waitForTimeout(800);
      if(Date.now()-lastBeat>30000){
        lastBeat=Date.now(); hb++;
        try{
          await popup.mouse.move(680+(hb%2?25:-25), 480);  // keepalive activity
          const di=JSON.parse(await domInfo(popup));
          const dead=/Secure Browser session|Sign in to your|Connecting\.\.\./.test(di.body||'') || (di.buttons||[]).includes('Sign in');
          fs.writeFileSync('/tmp/labs_heartbeat.json', JSON.stringify({ts:Date.now(),hb,dead,body:(di.body||'').slice(0,80)}));
          if(dead){ log('HEARTBEAT disconnect detected -> reconnect'); await reconnect(); }
        }catch(e){ log('hb err '+e.message); }
      }
      continue;
    }
    for(const f of files){
      const fp=CMD+'/'+f; let cmd;
      try{ cmd=JSON.parse(fs.readFileSync(fp,'utf8')); }catch(e){ try{fs.renameSync(fp,fp+'.bad');}catch(_){} continue; }
      log('CMD '+f+' '+JSON.stringify(cmd).slice(0,120));
      try{
        if(cmd.action==='click'){ await popup.mouse.click(cmd.x,cmd.y,{clickCount:cmd.count||1}); }
        else if(cmd.action==='dblclick'){ await popup.mouse.dblclick(cmd.x,cmd.y); }
        else if(cmd.action==='move'){ await popup.mouse.move(cmd.x,cmd.y); }
        else if(cmd.action==='type'){ await popup.keyboard.type(cmd.text, {delay: cmd.delay||10}); }
        else if(cmd.action==='key'){ await popup.keyboard.press(cmd.keys); }
        else if(cmd.action==='setclip'){ await popup.evaluate(t=>navigator.clipboard.writeText(t), cmd.text); }
        else if(cmd.action==='wait'){ await popup.waitForTimeout(cmd.ms||1000); }
        else if(cmd.action==='shot'){ await popup.screenshot({path: cmd.path||'/tmp/labs_now.png'}); }
        else if(cmd.action==='exit'){ running=false; }
        const shotPath = cmd.shot || (cmd.action!=='shot' ? '/tmp/labs_now.png' : null);
        if(shotPath && cmd.action!=='exit'){ await popup.screenshot({path:shotPath}).catch(()=>{}); }
        result({status:'ok', cmd, dom: await domInfo(popup)});
        log('CMD_OK '+f);
      }catch(e){ result({status:'err', cmd, error:e.message}); log('CMD_ERR '+f+' '+e.message); }
      try{ fs.renameSync(fp, fp+'.done'); }catch(e){}
    }
  }
  log('DONE - exiting command loop');
  await ctx.close();
  process.exit(0);
})().catch(e=>{ try{require('fs').appendFileSync('/tmp/labs_run.log','[labs] FATAL '+e.message+'\n');}catch(_){}; console.log('[labs] FATAL', e.message); process.exit(1); });
