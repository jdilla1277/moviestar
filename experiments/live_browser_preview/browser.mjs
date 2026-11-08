import {spawn} from 'node:child_process';
import {mkdtemp,readFile,rm,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
export const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export class Browser {
  static async launch(chrome,out){
    await mkdir(out,{recursive:true});
    const browser=new Browser();browser.profile=await mkdtemp(join(out,'browser-'));
    const flags=['--headless=new','--disable-gpu','--hide-scrollbars','--disable-background-networking','--disable-extensions','--no-first-run','--force-device-scale-factor=1','--remote-debugging-port=0',`--user-data-dir=${browser.profile}`,'about:blank'];
    if(process.getuid?.()===0||process.env.MOVIESTAR_CHROME_NO_SANDBOX==='1')flags.unshift('--no-sandbox');
    browser.child=spawn(chrome,flags,{stdio:['ignore','ignore','pipe'],detached:true});
    browser.error='';browser.child.stderr.on('data',chunk=>{browser.error=(browser.error+chunk).slice(-4000);});
    browser.exited=new Promise(resolve=>{browser.child.once('exit',resolve);browser.child.once('error',error=>{browser.launchError=error;resolve();});});
    browser.interrupt=async()=>{await browser.close();process.exitCode=130;};
    process.once('SIGINT',browser.interrupt);process.once('SIGTERM',browser.interrupt);
    try{
      for(let index=0;index<600&&!browser.port;index++){
        try{browser.port=Number((await readFile(join(browser.profile,'DevToolsActivePort'),'utf8')).split('\n')[0]);}catch{}
        if(browser.launchError)throw browser.launchError;
        if(browser.child.exitCode!==null)throw Error('Chrome exited: '+browser.error);
        if(!browser.port)await sleep(50);
      }
      if(!browser.port)throw Error('Chrome startup timed out: '+browser.error);
      let page;
      for(let index=0;index<100&&!page;index++){
        try{page=(await(await fetch(`http://127.0.0.1:${browser.port}/json/list`,{signal:AbortSignal.timeout(500)})).json()).find(item=>item.type==='page');}catch{}
        if(!page)await sleep(50);
      }
      if(!page)throw Error('Chrome did not expose a page');
      browser.socket=new WebSocket(page.webSocketDebuggerUrl);browser.next=0;browser.waiters=new Map();
      await new Promise((resolve,reject)=>{browser.socket.addEventListener('open',resolve,{once:true});browser.socket.addEventListener('error',reject,{once:true});});
      browser.socket.addEventListener('message',event=>{
        const value=JSON.parse(event.data);if(!value.id)return;const item=browser.waiters.get(value.id);if(!item)return;
        clearTimeout(item.timer);browser.waiters.delete(value.id);value.error?item.reject(Error(JSON.stringify(value.error))):item.resolve(value.result);
      });
      await browser.send('Page.enable');await browser.send('Runtime.enable');
      await browser.send('Emulation.setDeviceMetricsOverride',{width:1280,height:1000,deviceScaleFactor:1,mobile:false});
      return browser;
    }catch(error){await browser.close();throw error;}
  }
  send(method,params={}){
    return new Promise((resolve,reject)=>{const id=++this.next;const timer=setTimeout(()=>{this.waiters.delete(id);reject(Error(method+' timed out'));},10000);this.waiters.set(id,{resolve,reject,timer});this.socket.send(JSON.stringify({id,method,params}));});
  }
  async evaluate(expression){
    const result=await this.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
    if(result.exceptionDetails)throw Error(result.exceptionDetails.exception?.description??result.exceptionDetails.text);
    return result.result.value;
  }
  async wait(expression,timeout=10000){
    const started=Date.now();let last;
    while(Date.now()-started<timeout){try{last=await this.evaluate(expression);if(last)return last;}catch(error){last=error.message;}await sleep(20);}
    throw Error('Browser wait timed out: '+expression+'; '+JSON.stringify(last));
  }
  async click(selector){
    const point=await this.evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    await this.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});
    await this.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});
  }
  async close(){
    if(this.closed)return;this.closed=true;
    process.removeListener('SIGINT',this.interrupt);process.removeListener('SIGTERM',this.interrupt);
    this.socket?.close();
    for(const item of this.waiters?.values()??[]){clearTimeout(item.timer);item.reject(Error('Browser closed'));}this.waiters?.clear();
    const signal=value=>{if(!this.child?.pid)return;try{process.kill(-this.child.pid,value);}catch(error){if(!['ESRCH','EPERM'].includes(error.code))throw error;}};
    if(this.child){signal('SIGTERM');await Promise.race([this.exited,sleep(1000)]);signal('SIGKILL');await Promise.race([this.exited,sleep(1000)]);this.child.stderr?.destroy();}
    if(this.profile)await rm(this.profile,{recursive:true,force:true,maxRetries:3,retryDelay:100});
  }
}
