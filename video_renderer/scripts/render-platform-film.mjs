import {existsSync} from 'node:fs';
import {mkdir, readFile, unlink, writeFile} from 'node:fs/promises';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';

import {buildSrt} from '../src/contracts.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputDir = path.join(root, 'output', 'platform-film');
const manifest = JSON.parse(await readFile(path.join(outputDir, 'manifest.json'), 'utf8'));
const quality = process.argv.includes('--draft') ? 'draft' : 'final';
const inputProps = {manifest, quality};
const basename = quality === 'final' ? 'I-ONE_AI_平台宣传片_2026' : 'I-ONE_AI_平台宣传片_2026_预览';
const rawVideo = path.join(outputDir, `${basename}.raw.mp4`);
const finalVideo = path.join(outputDir, `${basename}.mp4`);

const ffmpegCandidates = [
  process.env.FFMPEG_PATH,
  path.join(root, 'node_modules', '@remotion', 'compositor-win32-x64-msvc', 'ffmpeg.exe'),
  path.join(root, 'node_modules', '@remotion', 'compositor-linux-arm64-gnu', 'ffmpeg'),
  path.join(root, 'node_modules', '@remotion', 'compositor-linux-x64-gnu', 'ffmpeg'),
].filter(Boolean);
const ffmpeg = ffmpegCandidates.find((candidate) => existsSync(candidate));
if (!ffmpeg) throw new Error('Remotion FFmpeg binary was not found');

const run = (command, args) => new Promise((resolve, reject) => {
  const child = spawn(command, args, {stdio: 'inherit'});
  child.once('error', reject);
  child.once('exit', (code) => code === 0 ? resolve() : reject(new Error(`${command} exited with code ${code}`)));
});

await mkdir(outputDir, {recursive: true});
const serveUrl = await bundle({entryPoint: path.join(root, 'src', 'remotion', 'index.ts'), enableCaching: false});
const composition = await selectComposition({serveUrl, id: 'PlatformFilm', inputProps, timeoutInMilliseconds: 120_000});

let lastProgress = -1;
await renderMedia({
  composition,
  serveUrl,
  codec: 'h264',
  audioCodec: 'aac',
  outputLocation: rawVideo,
  inputProps,
  crf: quality === 'final' ? 18 : 27,
  concurrency: '60%',
  timeoutInMilliseconds: 120_000,
  onProgress: ({progress}) => {
    const percent = Math.floor(progress * 100);
    if (percent >= lastProgress + 2) {
      lastProgress = percent;
      console.log(`render ${percent}%`);
    }
  },
});

await run(ffmpeg, [
  '-loglevel', 'warning',
  '-y',
  '-i', rawVideo,
  '-c:v', 'copy',
  '-af', 'loudnorm=I=-16:LRA=7:TP=-1.5',
  '-c:a', 'aac',
  '-b:a', '256k',
  finalVideo,
]);
await unlink(rawVideo);

await renderStill({
  composition,
  serveUrl,
  output: path.join(outputDir, `${basename}_封面.png`),
  imageFormat: 'png',
  inputProps,
  frame: composition.fps * 2,
  timeoutInMilliseconds: 120_000,
});

await writeFile(path.join(outputDir, `${basename}.srt`), buildSrt(manifest), 'utf8');
console.log(finalVideo);
