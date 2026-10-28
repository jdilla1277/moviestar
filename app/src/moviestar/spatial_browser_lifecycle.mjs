// Stop the owned browser before removing its profile. Cleanup reports its own
// error so a caller can preserve an earlier startup or rendering failure.
import {rmSync} from 'node:fs';

export async function stopBrowser(child, closed, profile, removeProfile = rmSync) {
  async function waitForClose(milliseconds) {
    let timer;
    try {
      await Promise.race([closed, new Promise(resolve => {
        timer = setTimeout(resolve, milliseconds);
      })]);
    } finally {
      clearTimeout(timer);
    }
  }
  if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
  await waitForClose(2000);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill('SIGKILL');
    await waitForClose(1000);
  }
  try {
    removeProfile(profile, {recursive: true, force: true, maxRetries: 3, retryDelay: 100});
    return null;
  } catch (error) {
    return error;
  }
}
