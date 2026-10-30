// Stop the owned browser before removing its profile. Cleanup reports its own
// error so a caller can preserve an earlier startup or rendering failure.
import {rmSync} from 'node:fs';

export async function stopBrowser(child, exited, profile, removeProfile = rmSync) {
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
  if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
  await waitForExit(2000);
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
