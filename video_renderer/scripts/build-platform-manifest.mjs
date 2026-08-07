import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const platformDir = path.join(root, 'assets', 'platform');
const source = JSON.parse(await readFile(path.join(platformDir, 'platform-film.source.json'), 'utf8'));

const asDataUri = async (file) => {
  const extension = path.extname(file).toLowerCase();
  const mime = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
  }[extension];
  if (!mime) throw new Error(`unsupported platform asset: ${file}`);
  return `data:${mime};base64,${(await readFile(file)).toString('base64')}`;
};

const manifest = {
  ...source,
  duration_seconds: source.scenes.reduce((total, scene) => total + Number(scene.duration_seconds), 0),
  music_data_uri: await asDataUri(path.join(platformDir, 'audio', 'platform-bed.wav')),
  scenes: [],
};

for (const scene of source.scenes) {
  const built = {...scene};
  if (scene.asset) {
    built.asset_name = path.basename(scene.asset);
    built.asset_data_uri = await asDataUri(path.join(platformDir, scene.asset));
    delete built.asset;
  }
  built.narration_audio_data_uri = await asDataUri(path.join(platformDir, 'audio', 'voice', `${scene.code}.mp3`));
  manifest.scenes.push(built);
}

const outputDir = path.join(root, 'output', 'platform-film');
await mkdir(outputDir, {recursive: true});
const output = path.join(outputDir, 'manifest.json');
await writeFile(output, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
await writeFile(path.join(outputDir, 'props-draft.json'), `${JSON.stringify({manifest, quality: 'draft'})}\n`, 'utf8');
await writeFile(path.join(outputDir, 'props-final.json'), `${JSON.stringify({manifest, quality: 'final'})}\n`, 'utf8');
await writeFile(
  path.join(outputDir, 'request-draft.json'),
  `${JSON.stringify({reference: 'IONE-PLATFORM-2026', quality: 'draft', manifest})}\n`,
  'utf8',
);
console.log(output);
