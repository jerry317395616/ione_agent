import {createReadStream, existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync} from 'node:fs';
import {createServer} from 'node:http';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {randomUUID, timingSafeEqual} from 'node:crypto';

import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';

import {buildSrt, validateRenderRequest} from './contracts.mjs';

const host = process.env.IONE_VIDEO_HOST || '127.0.0.1';
const port = Number(process.env.IONE_VIDEO_PORT || 8120);
const token = process.env.IONE_VIDEO_TOKEN || '';
const dataDir = path.resolve(process.env.IONE_VIDEO_DATA_DIR || '/data');
const sourceDir = path.dirname(fileURLToPath(import.meta.url));
const entryPoint = path.join(sourceDir, 'remotion', 'index.ts');
const queue = [];
let processing = false;
let bundlePromise;

if (!token) throw new Error('IONE_VIDEO_TOKEN is required');
mkdirSync(dataDir, {recursive: true, mode: 0o700});

const safeEqual = (left, right) => {
  const a = Buffer.from(left || '');
  const b = Buffer.from(right || '');
  return a.length === b.length && timingSafeEqual(a, b);
};

const authorize = (request) => {
  const supplied = String(request.headers.authorization || '').replace(/^Bearer\s+/i, '');
  return safeEqual(supplied, token);
};

const jobDir = (id) => path.join(dataDir, id);
const jobPath = (id) => path.join(jobDir(id), 'job.json');
const requestPath = (id) => path.join(jobDir(id), 'request.json');
const artifactPath = (id, key) => {
  const names = {video: 'video.mp4', cover: 'cover.png', subtitles: 'subtitles.srt'};
  if (!names[key]) throw new Error('unknown artifact');
  return path.join(jobDir(id), names[key]);
};

const readJob = (id) => JSON.parse(readFileSync(jobPath(id), 'utf8'));
const writeJob = (job) => {
  writeFileSync(jobPath(job.id), `${JSON.stringify(job, null, 2)}\n`, {encoding: 'utf8', mode: 0o600});
};

const publicJob = (job) => {
  const artifacts = {};
  if (job.status === 'completed') {
    for (const key of ['video', 'cover', 'subtitles']) {
      const file = artifactPath(job.id, key);
      if (existsSync(file)) {
        artifacts[key] = {
          size: statSync(file).size,
          url: `/v1/jobs/${encodeURIComponent(job.id)}/artifacts/${key}`,
        };
      }
    }
  }
  return {
    job_id: job.id,
    reference: job.reference,
    quality: job.quality,
    status: job.status,
    progress: job.progress,
    step: job.step,
    error: job.error || '',
    created_at: job.created_at,
    updated_at: job.updated_at,
    artifacts,
  };
};

const getBundle = () => {
  if (!bundlePromise) bundlePromise = bundle({entryPoint, enableCaching: false});
  return bundlePromise;
};

const renderJob = async (id) => {
  const request = JSON.parse(readFileSync(requestPath(id), 'utf8'));
  const job = readJob(id);
  job.status = 'rendering';
  job.progress = 2;
  job.step = '加载视频模板';
  job.updated_at = new Date().toISOString();
  writeJob(job);
  const serveUrl = await getBundle();
  const inputProps = {manifest: request.manifest, quality: request.quality};
  const composition = await selectComposition({serveUrl, id: 'DealPromo', inputProps, timeoutInMilliseconds: 120000});
  job.step = '生成视频画面';
  job.progress = 5;
  writeJob(job);
  let lastProgress = 5;
  await renderMedia({
    composition,
    serveUrl,
    codec: 'h264',
    audioCodec: 'aac',
    outputLocation: artifactPath(id, 'video'),
    inputProps,
    crf: request.quality === 'final' ? 20 : 28,
    concurrency: '50%',
    timeoutInMilliseconds: 120000,
    onProgress: ({progress}) => {
      const value = Math.max(5, Math.min(92, Math.round(5 + progress * 87)));
      if (value >= lastProgress + 2) {
        lastProgress = value;
        job.progress = value;
        job.updated_at = new Date().toISOString();
        writeJob(job);
      }
    },
  });
  job.step = '生成封面与字幕';
  job.progress = 94;
  writeJob(job);
  await renderStill({
    composition,
    serveUrl,
    output: artifactPath(id, 'cover'),
    imageFormat: 'png',
    inputProps,
    frame: Math.min(composition.durationInFrames - 1, composition.fps),
    timeoutInMilliseconds: 120000,
  });
  writeFileSync(artifactPath(id, 'subtitles'), buildSrt(request.manifest), {encoding: 'utf8', mode: 0o600});
  job.status = 'completed';
  job.progress = 100;
  job.step = '渲染完成';
  job.updated_at = new Date().toISOString();
  writeJob(job);
};

const processQueue = async () => {
  if (processing) return;
  processing = true;
  while (queue.length) {
    const id = queue.shift();
    try {
      await renderJob(id);
    } catch (error) {
      const job = readJob(id);
      job.status = 'failed';
      job.step = '渲染失败';
      job.error = String(error?.stack || error).slice(0, 4000);
      job.updated_at = new Date().toISOString();
      writeJob(job);
    }
  }
  processing = false;
};

const jsonResponse = (response, status, payload) => {
  const body = Buffer.from(JSON.stringify(payload));
  response.writeHead(status, {'content-type': 'application/json; charset=utf-8', 'content-length': body.length});
  response.end(body);
};

const readJson = (request) => new Promise((resolve, reject) => {
  const chunks = [];
  let size = 0;
  request.on('data', (chunk) => {
    size += chunk.length;
    if (size > 25 * 1024 * 1024) {
      reject(new Error('request body exceeds 25 MB'));
      request.destroy();
      return;
    }
    chunks.push(chunk);
  });
  request.on('end', () => {
    try {
      resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
    } catch (error) {
      reject(error);
    }
  });
  request.on('error', reject);
});

const serveArtifact = (response, id, key) => {
  if (!/^[a-f0-9-]{36}$/.test(id)) return jsonResponse(response, 404, {error: 'job not found'});
  const file = artifactPath(id, key);
  if (!existsSync(file)) return jsonResponse(response, 404, {error: 'artifact not found'});
  const contentTypes = {video: 'video/mp4', cover: 'image/png', subtitles: 'application/x-subrip; charset=utf-8'};
  response.writeHead(200, {'content-type': contentTypes[key], 'content-length': statSync(file).size});
  createReadStream(file).pipe(response);
};

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url || '/', `http://${request.headers.host || 'localhost'}`);
    if (request.method === 'GET' && url.pathname === '/health') {
      return jsonResponse(response, 200, {status: 'ok', service: 'ione-video-renderer', queue: queue.length, processing});
    }
    if (!url.pathname.startsWith('/v1/') || !authorize(request)) {
      return jsonResponse(response, 401, {error: 'unauthorized'});
    }
    if (request.method === 'POST' && url.pathname === '/v1/jobs') {
      const validated = validateRenderRequest(await readJson(request));
      const id = randomUUID();
      mkdirSync(jobDir(id), {recursive: false, mode: 0o700});
      writeFileSync(requestPath(id), `${JSON.stringify(validated)}\n`, {encoding: 'utf8', mode: 0o600});
      const now = new Date().toISOString();
      writeJob({
        id,
        reference: validated.reference,
        quality: validated.quality,
        status: 'queued',
        progress: 0,
        step: '等待渲染',
        error: '',
        created_at: now,
        updated_at: now,
      });
      queue.push(id);
      void processQueue();
      return jsonResponse(response, 202, {job_id: id, status: 'queued'});
    }
    const statusMatch = url.pathname.match(/^\/v1\/jobs\/([a-f0-9-]{36})$/);
    if (request.method === 'GET' && statusMatch) {
      if (!existsSync(jobPath(statusMatch[1]))) return jsonResponse(response, 404, {error: 'job not found'});
      return jsonResponse(response, 200, publicJob(readJob(statusMatch[1])));
    }
    const artifactMatch = url.pathname.match(/^\/v1\/jobs\/([a-f0-9-]{36})\/artifacts\/(video|cover|subtitles)$/);
    if (request.method === 'GET' && artifactMatch) return serveArtifact(response, artifactMatch[1], artifactMatch[2]);
    return jsonResponse(response, 404, {error: 'not found'});
  } catch (error) {
    return jsonResponse(response, 400, {error: String(error?.message || error)});
  }
});

for (const name of readdirSync(dataDir)) {
  const file = jobPath(name);
  if (!existsSync(file)) continue;
  try {
    const job = readJob(name);
    if (job.status === 'queued' || job.status === 'rendering') {
      job.status = 'queued';
      job.progress = 0;
      job.step = '服务恢复后重新排队';
      job.updated_at = new Date().toISOString();
      writeJob(job);
      queue.push(name);
    }
  } catch {
    // Ignore incomplete directories; they are never exposed as valid jobs.
  }
}

server.listen(port, host, () => {
  console.log(`I-ONE video renderer listening on http://${host}:${port}`);
  void processQueue();
});
