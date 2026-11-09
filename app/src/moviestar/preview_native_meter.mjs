class CueMeter extends AudioWorkletProcessor {
  constructor(){super();this.active=false;}
  process(inputs,outputs){
    const input=inputs[0]??[], output=outputs[0]??[];
    for(let channel=0;channel<output.length;channel++){
      const samples=input[channel]??input[0];
      if(samples)output[channel].set(samples);else output[channel].fill(0);
    }
    const samples=input[0];let energy=0;
    if(samples)for(const value of samples)energy+=value*value;
    const rms=samples?Math.sqrt(energy/samples.length):0,active=rms>.03;
    if(active&&!this.active)this.port.postMessage({context_time:currentFrame/sampleRate,rms});
    this.active=active;
    return true;
  }
}
registerProcessor('cue-meter',CueMeter);
