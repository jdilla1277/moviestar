// Passthrough mix meter. Per-quantum diagnostics are opt-in, local benchmark data.
class CutOutputMeter extends AudioWorkletProcessor {
  constructor(options){super();this.audit=options.processorOptions?.audit===true;this.active=false;}
  process(inputs,outputs){
    const input=inputs[0]??[],output=outputs[0]??[];
    for(let i=0;i<output.length;i++){
      const data=input[i]??input[0];if(data)output[i].set(data);else output[i].fill(0);
    }
    const left=output[0];let leftEnergy=0;
    if(left)for(const value of left)leftEnergy+=value*value;
    const rms=left?Math.sqrt(leftEnergy/left.length):0,active=rms>.03;
    if(active&&!this.active)this.port.postMessage({kind:'cue',context_time:currentFrame/sampleRate,rms});
    this.active=active;
    if(this.audit){
      // The synthetic fixture's right channel is a continuous pilot tone.
      const samples=output[1]??output[0];let energy=0;
      if(samples)for(const value of samples)energy+=value*value;
      this.port.postMessage({kind:'block',context_time:currentFrame/sampleRate,rms:samples?Math.sqrt(energy/samples.length):0,
        duration:samples?samples.length/sampleRate:0});
    }
    return true;
  }
}
registerProcessor('cut-output-meter',CutOutputMeter);
