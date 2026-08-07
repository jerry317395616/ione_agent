import {mkdir, readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

import {bundle} from '@remotion/bundler';
import {renderStill, selectComposition} from '@remotion/renderer';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputDir = path.join(root, 'output', 'platform-film', 'qa');
const manifest = JSON.parse(await readFile(path.join(root, 'output', 'platform-film', 'manifest.json'), 'utf8'));
const inputProps = {manifest, quality: 'draft'};

await mkdir(outputDir, {recursive: true});
const serveUrl = await bundle({entryPoint: path.join(root, 'src', 'remotion', 'index.ts'), enableCaching: false});
const composition = await selectComposition({serveUrl, id: 'PlatformFilm', inputProps, timeoutInMilliseconds: 120_000});

let cursor = 0;
for (let index = 0; index < manifest.scenes.length; index += 1) {
  const scene = manifest.scenes[index];
  const sceneFrames = Math.round(scene.duration_seconds * composition.fps);
  const frame = Math.min(composition.durationInFrames - 1, cursor + Math.min(sceneFrames - 1, composition.fps * 2));
  const file = path.join(outputDir, `${String(index + 1).padStart(2, '0')}-${scene.kind}.png`);
  await renderStill({
    composition,
    serveUrl,
    output: file,
    imageFormat: 'png',
    inputProps,
    frame,
    timeoutInMilliseconds: 120_000,
  });
  console.log(`${index + 1}/${manifest.scenes.length} ${scene.kind} frame=${frame}`);
  cursor += sceneFrames;
}
