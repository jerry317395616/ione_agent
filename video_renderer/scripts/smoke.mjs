import {spawn} from 'node:child_process';
import {readFileSync} from 'node:fs';
import path from 'node:path';

const port = 18120;
const token = `smoke-${Date.now()}`;
const dataDir = path.resolve(`.smoke-data-${Date.now()}`);
const child = spawn(process.execPath, ['src/server.mjs'], {
  cwd: process.cwd(),
  env: {
    ...process.env,
    IONE_VIDEO_HOST: '127.0.0.1',
    IONE_VIDEO_PORT: String(port),
    IONE_VIDEO_TOKEN: token,
    IONE_VIDEO_DATA_DIR: dataDir,
  },
  stdio: ['ignore', 'pipe', 'pipe'],
});

let stderr = '';
let stdout = '';
child.stdout.on('data', (chunk) => {
  stdout += chunk.toString();
});
child.stderr.on('data', (chunk) => {
  stderr += chunk.toString();
});

const headers = {authorization: `Bearer ${token}`};
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

const waitForHealth = async () => {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (child.exitCode !== null) {
      throw new Error(`renderer exited with code ${child.exitCode}\n${stdout}\n${stderr}`);
    }
    try {
      const response = await fetch(`http://127.0.0.1:${port}/health`);
      if (response.ok) return;
    } catch {
      // The renderer may still be starting.
    }
    await sleep(500);
  }
  throw new Error(`renderer did not become healthy\n${stdout}\n${stderr}`);
};

try {
  await waitForHealth();
  const request = JSON.parse(readFileSync('testdata/sample-request.json', 'utf8'));
  const queued = await fetch(`http://127.0.0.1:${port}/v1/jobs`, {
    method: 'POST',
    headers: {...headers, 'content-type': 'application/json'},
    body: JSON.stringify(request),
  });
  if (!queued.ok) throw new Error(await queued.text());
  const {job_id: jobId} = await queued.json();
  let result;
  for (let attempt = 0; attempt < 240; attempt += 1) {
    await sleep(2000);
    const response = await fetch(`http://127.0.0.1:${port}/v1/jobs/${jobId}`, {headers});
    result = await response.json();
    if (attempt % 5 === 0) console.log(`${result.status} ${result.progress}% ${result.step}`);
    if (result.status === 'completed' || result.status === 'failed') break;
  }
  if (result?.status !== 'completed') {
    throw new Error(`${result?.error || 'render did not complete'}\n${stderr}`);
  }
  for (const key of ['video', 'cover', 'subtitles']) {
    if (!result.artifacts[key]?.size) throw new Error(`missing ${key} artifact`);
  }
  console.log(JSON.stringify({job_id: jobId, data_dir: dataDir, artifacts: result.artifacts}, null, 2));
} catch (error) {
  throw new Error(`${error?.stack || error}\n--- renderer stdout ---\n${stdout}\n--- renderer stderr ---\n${stderr}`);
} finally {
  child.kill('SIGTERM');
}
