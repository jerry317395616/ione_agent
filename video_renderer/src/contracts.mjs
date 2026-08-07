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

const platformKinds = new Set([
  'brand',
  'tension',
  'operating-system',
  'ai',
  'business',
  'people',
  'growth',
  'collaboration',
  'insight',
  'healthcare',
  'insurance',
  'quality',
  'screening',
  'education',
  'ecosystem',
  'trust',
  'closing',
]);

const clean = (value, maximum, field, required = false) => {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  if (required && !text) throw new Error(`${field} is required`);
  if (text.length > maximum) throw new Error(`${field} exceeds ${maximum} characters`);
  return text;
};

const cleanList = (value, maximumItems, maximumLength, field) => {
  const list = Array.isArray(value) ? value : [];
  if (list.length > maximumItems) throw new Error(`${field} has too many items`);
  return list.map((item, index) => clean(item, maximumLength, `${field} ${index + 1}`, true));
};

const cleanImage = (value, field) => {
  const data = clean(value, 12_000_000, field);
  if (data && !/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/i.test(data)) {
    throw new Error(`${field} is not an allowed image data URI`);
  }
  return data;
};

const cleanAudio = (value, maximum, field) => {
  const data = clean(value, maximum, field);
  if (data && !/^data:audio\/(mpeg|mp3|wav|x-wav);base64,[A-Za-z0-9+/=]+$/i.test(data)) {
    throw new Error(`${field} is not an allowed audio data URI`);
  }
  return data;
};

const validatePlatformManifest = (source) => {
  if (!Array.isArray(source.scenes) || source.scenes.length < 14 || source.scenes.length > 22) {
    throw new Error('platform film must contain 14 to 22 scenes');
  }
  const scenes = source.scenes.map((scene, index) => {
    if (!scene || typeof scene !== 'object' || Array.isArray(scene)) {
      throw new Error(`scene ${index + 1} must be an object`);
    }
    const kind = clean(scene.kind, 32, `scene ${index + 1} kind`, true);
    if (!platformKinds.has(kind)) throw new Error(`scene ${index + 1} kind is unsupported`);
    const durationSeconds = Number(scene.duration_seconds ?? 8);
    if (!Number.isFinite(durationSeconds) || durationSeconds < 5 || durationSeconds > 20) {
      throw new Error(`scene ${index + 1} duration is invalid`);
    }
    return {
      code: clean(scene.code || `scene-${index + 1}`, 32, `scene ${index + 1} code`, true),
      kind,
      kicker: clean(scene.kicker, 80, `scene ${index + 1} kicker`),
      title: clean(scene.title, 120, `scene ${index + 1} title`, true),
      subtitle: clean(scene.subtitle, 220, `scene ${index + 1} subtitle`),
      bullets: cleanList(scene.bullets, 6, 80, `scene ${index + 1} bullets`),
      apps: cleanList(scene.apps, 10, 60, `scene ${index + 1} apps`),
      layers: cleanList(scene.layers, 6, 80, `scene ${index + 1} layers`),
      actions: cleanList(scene.actions, 6, 100, `scene ${index + 1} actions`),
      command: clean(scene.command, 240, `scene ${index + 1} command`),
      narration: clean(scene.narration, 500, `scene ${index + 1} narration`, true),
      duration_seconds: Math.round(durationSeconds * 100) / 100,
      asset_data_uri: cleanImage(scene.asset_data_uri, `scene ${index + 1} asset`),
      asset_name: clean(scene.asset_name, 180, `scene ${index + 1} asset name`),
      narration_audio_data_uri: cleanAudio(scene.narration_audio_data_uri, 2_000_000, `scene ${index + 1} narration audio`),
    };
  });
  if (scenes[0].kind !== 'brand' || scenes.at(-1).kind !== 'closing') {
    throw new Error('platform film must begin with brand and end with closing');
  }
  const durationSeconds = scenes.reduce((total, scene) => total + scene.duration_seconds, 0);
  if (durationSeconds < 120 || durationSeconds > 240) throw new Error('platform film total duration is invalid');
  const metrics = Array.isArray(source.metrics) ? source.metrics : [];
  if (metrics.length < 1 || metrics.length > 6) throw new Error('platform film metrics are invalid');
  return {
    schema_version: 1,
    format: 'platform-film-v1',
    title: clean(source.title, 140, 'title', true),
    brand: clean(source.brand || 'I-ONE AI', 80, 'brand'),
    tagline: clean(source.tagline, 180, 'tagline'),
    aspect_ratio: '16:9',
    language: clean(source.language || 'zh-CN', 16, 'language'),
    show_subtitles: source.show_subtitles !== false,
    duration_seconds: Math.round(durationSeconds * 100) / 100,
    metrics: metrics.map((metric, index) => ({
      value: clean(metric?.value, 20, `metric ${index + 1} value`, true),
      label: clean(metric?.label, 40, `metric ${index + 1} label`, true),
    })),
    music_data_uri: cleanAudio(source.music_data_uri, 14_000_000, 'music'),
    scenes,
  };
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
  if (source.format === 'platform-film-v1') {
    return {
      reference,
      quality,
      composition_id: 'PlatformFilm',
      manifest: validatePlatformManifest(source),
    };
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
    const assetDataUri = cleanImage(scene.asset_data_uri, `scene ${index + 1} asset`);
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
    composition_id: 'DealPromo',
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
