// Stop the owned browser before removing its profile. Cleanup reports its own
// error so a caller can preserve an earlier startup or rendering failure.
import {rmSync} from 'node:fs';

export async function stopBrowser(child, exited, profile, removeProfile = rmSync, requestClose) {
  async function waitForExit(milliseconds) {
    let timer;
    try {
      await Promise.race([exited, new Promise(resolve => {
        timer = setTimeout(resolve, milliseconds);
      })]);
    } finally {
      clearTimeout(timer);
    }
  }
  const deadline = performance.now() + 2000;
  if (requestClose && child.exitCode === null && child.signalCode === null) {
    // Browser.close lets Chrome drain profile writers before its parent exits.
    // A reply is not an exit acknowledgement. Wait for exit, bounded inside
    // the existing two-second termination budget, then retain forced fallback.
    Promise.resolve().then(requestClose).catch(() => {});
    await waitForExit(500);
  }
  if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
  await waitForExit(Math.max(0, deadline - performance.now()));
  if (child.exitCode === null && child.signalCode === null) {
    child.kill('SIGKILL');
    await waitForExit(1000);
  }
  if (child.exitCode === null && child.signalCode === null) {
    return new Error('Chrome did not exit after SIGKILL');
  }
  // Stop reading the captured pipe after its owner exits. Otherwise an inherited
  // helper descriptor can keep Node alive until the Python shutdown watchdog
  // fires. The Python owner reaps remaining descendants in its process group.
  child.stderr?.destroy();
  try {
    removeProfile(profile, {recursive: true, force: true, maxRetries: 3, retryDelay: 100});
    return null;
  } catch (error) {
    return error;
  }
}
