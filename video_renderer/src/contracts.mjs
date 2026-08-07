const allowedKinds = new Set([
  'cover',
  'context',
  'challenge',
  'solution',
  'capability',
  'roadmap',
  'value',
  'closing',
]);

const clean = (value, maximum, field, required = false) => {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  if (required && !text) throw new Error(`${field} is required`);
  if (text.length > maximum) throw new Error(`${field} exceeds ${maximum} characters`);
  return text;
};

export const validateRenderRequest = (request) => {
  if (!request || typeof request !== 'object' || Array.isArray(request)) {
    throw new Error('request must be an object');
  }
  const reference = clean(request.reference, 80, 'reference', true);
  if (!/^[A-Za-z0-9_-]+$/.test(reference)) throw new Error('reference contains invalid characters');
  const quality = request.quality === 'final' ? 'final' : 'draft';
  const source = request.manifest;
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    throw new Error('manifest must be an object');
  }
  if (!Array.isArray(source.scenes) || source.scenes.length < 6 || source.scenes.length > 12) {
    throw new Error('manifest must contain 6 to 12 scenes');
  }
  const scenes = source.scenes.map((scene, index) => {
    if (!scene || typeof scene !== 'object' || Array.isArray(scene)) {
      throw new Error(`scene ${index + 1} must be an object`);
    }
    const kind = clean(scene.kind, 24, `scene ${index + 1} kind`, true);
    if (!allowedKinds.has(kind)) throw new Error(`scene ${index + 1} kind is unsupported`);
    const bullets = Array.isArray(scene.bullets) ? scene.bullets : [];
    if (bullets.length > 6) throw new Error(`scene ${index + 1} has too many bullets`);
    const durationSeconds = Number(scene.duration_seconds ?? 8);
    if (!Number.isFinite(durationSeconds) || durationSeconds < 3 || durationSeconds > 30) {
      throw new Error(`scene ${index + 1} duration is invalid`);
    }
    const assetDataUri = clean(scene.asset_data_uri, 8_000_000, `scene ${index + 1} asset`);
    if (assetDataUri && !/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/i.test(assetDataUri)) {
      throw new Error(`scene ${index + 1} asset is not an allowed image data URI`);
    }
    return {
      code: clean(scene.code || `scene-${index + 1}`, 32, `scene ${index + 1} code`, true),
      kind,
      title: clean(scene.title, 100, `scene ${index + 1} title`, true),
      subtitle: clean(scene.subtitle, 220, `scene ${index + 1} subtitle`),
      bullets: bullets.map((item) => clean(item, 140, `scene ${index + 1} bullet`, true)),
      narration: clean(scene.narration, 800, `scene ${index + 1} narration`),
      duration_seconds: Math.round(durationSeconds * 100) / 100,
      asset_data_uri: assetDataUri,
      asset_name: clean(scene.asset_name, 180, `scene ${index + 1} asset name`),
      evidence: clean(scene.evidence, 500, `scene ${index + 1} evidence`),
    };
  });
  if (scenes[0].kind !== 'cover' || scenes.at(-1).kind !== 'closing') {
    throw new Error('first scene must be cover and last scene must be closing');
  }
  const durationSeconds = scenes.reduce((total, scene) => total + scene.duration_seconds, 0);
  if (durationSeconds < 30 || durationSeconds > 180) throw new Error('total duration is invalid');
  const aspectRatio = source.aspect_ratio === '9:16' ? '9:16' : '16:9';
  return {
    reference,
    quality,
    manifest: {
      schema_version: 1,
      title: clean(source.title, 140, 'title', true),
      customer: clean(source.customer, 140, 'customer'),
      brand: clean(source.brand || 'I-ONE AI', 80, 'brand'),
      template: source.template === 'enterprise' ? 'enterprise' : 'medical-enterprise',
      aspect_ratio: aspectRatio,
      language: clean(source.language || 'zh-CN', 16, 'language'),
      call_to_action: clean(source.call_to_action, 180, 'call_to_action'),
      duration_seconds: Math.round(durationSeconds * 100) / 100,
      scenes,
    },
  };
};

const timestamp = (seconds) => {
  const milliseconds = Math.max(0, Math.round(seconds * 1000));
  const hours = Math.floor(milliseconds / 3_600_000);
  const minutes = Math.floor((milliseconds % 3_600_000) / 60_000);
  const secs = Math.floor((milliseconds % 60_000) / 1000);
  const ms = milliseconds % 1000;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')},${String(ms).padStart(3, '0')}`;
};

export const buildSrt = (manifest) => {
  let cursor = 0;
  const blocks = [];
  manifest.scenes.forEach((scene, index) => {
    const end = cursor + scene.duration_seconds;
    const text = scene.narration || [scene.title, scene.subtitle].filter(Boolean).join('。');
    if (text) blocks.push(`${index + 1}\n${timestamp(cursor)} --> ${timestamp(end)}\n${text}`);
    cursor = end;
  });
  return `${blocks.join('\n\n')}\n`;
};
