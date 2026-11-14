// Passthrough mix meter. Per-quantum diagnostics are opt-in, local benchmark data.
class CutOutputMeter extends AudioWorkletProcessor {
  constructor(options){
    super();this.audit=options.processorOptions?.audit===true;this.capturePcm=options.processorOptions?.capturePcm===true;this.active=false;this.blocks=[];this.capacity=11*48000;this.pcm=null;this.pcmFrames=0;this.captureActive=false;this.nextCaptureFrame=null;
    this.port.onmessage=({data})=>{
      if(data.kind==='capture'&&this.capturePcm){this.pcm=new Float32Array(this.capacity*2);this.pcmFrames=0;this.captureActive=true;return;}
      if(data.kind!=='flush')return;
      if(this.captureActive&&this.pcm){
        this.port.postMessage({kind:'pcm',start_context_time:this.pcmStart,sample_rate:sampleRate,frames:this.pcmFrames,capacity:this.capacity,data:this.pcm.buffer},[this.pcm.buffer]);
        this.pcm=null;this.captureActive=false;
      }
      this.flush();this.port.postMessage({kind:'flushed',id:data.id});
    };
  }
  flush(){
    if(!this.blocks.length)return;
    this.port.postMessage({kind:'blocks',blocks:this.blocks});this.blocks=[];
  }
  process(inputs,outputs){
    const input=inputs[0]??[],output=outputs[0]??[];
    for(let i=0;i<output.length;i++){
      const data=input[i]??input[0];if(data)output[i].set(data);else output[i].fill(0);
    }
    const left=output[0];let leftEnergy=0;
    // Capture-only clock: a browser can repeat a render stamp and recover on
    // the next callback. The PCM stream still contains distinct consecutive
    // sample blocks. Preserve raw phase/meter timestamps; only the capture
    // start uses its rendered-sample cursor. Forward gaps are not filled.
    const captureFrame=this.capturePcm?Math.max(currentFrame,this.nextCaptureFrame??currentFrame):currentFrame;
    if(this.capturePcm)this.nextCaptureFrame=captureFrame+(left?.length??0);
    if(left)for(const value of left)leftEnergy+=value*value;
    const rms=left?Math.sqrt(leftEnergy/left.length):0,active=rms>.03;
    if(active&&!this.active)this.port.postMessage({kind:'cue',context_time:currentFrame/sampleRate,rms});
    this.active=active;
    if(this.audit){
      // The synthetic fixture's right channel is a continuous pilot tone.
      const samples=output[1]??output[0];let energy=0;
      if(samples)for(const value of samples)energy+=value*value;
      if(this.captureActive&&this.pcm&&this.pcmFrames+left.length<=this.capacity){
        if(!this.pcmFrames)this.pcmStart=captureFrame/sampleRate;
        this.pcm.set(output[0],this.pcmFrames);this.pcm.set(output[1]??output[0],this.capacity+this.pcmFrames);
        this.pcmFrames+=left.length;
      }
      this.blocks.push({kind:'block',context_time:currentFrame/sampleRate,rms:samples?Math.sqrt(energy/samples.length):0,
        duration:samples?samples.length/sampleRate:0,
        ...(this.capturePcm?{reported_context_time:currentFrame/sampleRate,capture_context_time:captureFrame/sampleRate,clock_adjustment_frames:captureFrame-currentFrame}: {})});
      // Preserve every quantum but avoid hundreds of main-thread IPC tasks.
      if(this.blocks.length>=8)this.flush();
    }
    return true;
  }
}
registerProcessor('cut-output-meter',CutOutputMeter);
