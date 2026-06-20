const { chromium } = require('playwright-core');
const fs=require('fs');
(async()=>{
  fs.writeFileSync('/tmp/pw_test.txt','START\n');
  const ctx = await chromium.launchPersistentContext('/tmp/labs_pw_min', {channel:'chrome', headless:false, viewport:{width:1000,height:700}, ignoreDefaultArgs:['--disable-gpu'], args:['--no-first-run','--no-default-browser-check']});
  fs.appendFileSync('/tmp/pw_test.txt','LAUNCHED\n');
  const page = ctx.pages()[0]||await ctx.newPage();
  await page.goto('https://example.com',{timeout:30000});
  await page.screenshot({path:'/tmp/pw_test.png'});
  fs.appendFileSync('/tmp/pw_test.txt','SHOT title='+(await page.title())+'\n');
  await ctx.close();
  fs.appendFileSync('/tmp/pw_test.txt','DONE\n');
  process.exit(0);
})().catch(e=>{fs.appendFileSync('/tmp/pw_test.txt','ERR '+e.message+'\n');process.exit(1);});
