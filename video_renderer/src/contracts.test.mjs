import assert from 'node:assert/strict';
import test from 'node:test';

import {buildSrt, validateRenderRequest} from './contracts.mjs';

const manifest = {
  title: '区域医疗智能运营平台',
  customer: '示例客户',
  template: 'medical-enterprise',
  aspect_ratio: '16:9',
  scenes: [
    {kind: 'cover', title: '共同目标', narration: '从共同目标开始。', duration_seconds: 5},
    {kind: 'context', title: '客户现状', bullets: ['数据分散'], duration_seconds: 5},
    {kind: 'challenge', title: '核心痛点', bullets: ['协同效率待提升'], duration_seconds: 5},
    {kind: 'solution', title: '解决方案', bullets: ['统一运营平台'], duration_seconds: 5},
    {kind: 'roadmap', title: '实施路径', bullets: ['调研', '试点'], duration_seconds: 5},
    {kind: 'closing', title: '下一步', narration: '确认试点范围。', duration_seconds: 5},
  ],
};

test('validates a bounded promotional video request', () => {
  const result = validateRenderRequest({reference: 'IDV-2026-00001', quality: 'final', manifest});
  assert.equal(result.quality, 'final');
  assert.equal(result.manifest.duration_seconds, 30);
  assert.equal(result.manifest.scenes.length, 6);
});

test('rejects executable or unsupported scene kinds', () => {
  const changed = structuredClone(manifest);
  changed.scenes[2].kind = 'javascript';
  assert.throws(() => validateRenderRequest({reference: 'IDV-1', manifest: changed}), /unsupported/);
});

test('builds deterministic subtitles from scene timing', () => {
  const validated = validateRenderRequest({reference: 'IDV-1', manifest}).manifest;
  const srt = buildSrt(validated);
  assert.match(srt, /00:00:00,000 --> 00:00:05,000/);
  assert.match(srt, /确认试点范围/);
});
