import React from 'react';
import {Composition} from 'remotion';

import {DealPromo} from './video';
import {PlatformFilm} from './platform';

const defaultManifest = {
  title: 'I-ONE AI 客户解决方案',
  customer: '客户名称',
  brand: 'I-ONE AI',
  template: 'medical-enterprise',
  aspect_ratio: '16:9',
  language: 'zh-CN',
  call_to_action: '确认下一步合作计划',
  duration_seconds: 36,
  scenes: [
    {code: 'scene-01', kind: 'cover', title: '客户解决方案', subtitle: '', bullets: [], narration: '', duration_seconds: 6},
    {code: 'scene-02', kind: 'context', title: '客户现状', subtitle: '', bullets: [], narration: '', duration_seconds: 6},
    {code: 'scene-03', kind: 'challenge', title: '核心痛点', subtitle: '', bullets: [], narration: '', duration_seconds: 6},
    {code: 'scene-04', kind: 'solution', title: '解决方案', subtitle: '', bullets: [], narration: '', duration_seconds: 6},
    {code: 'scene-05', kind: 'roadmap', title: '实施路径', subtitle: '', bullets: [], narration: '', duration_seconds: 6},
    {code: 'scene-06', kind: 'closing', title: '携手推进下一步', subtitle: '', bullets: [], narration: '', duration_seconds: 6},
  ],
};

const platformDefault = {
  format: 'platform-film-v1',
  title: 'I-ONE AI 平台宣传片',
  brand: 'I-ONE AI',
  tagline: '让复杂组织，像一个系统一样行动',
  aspect_ratio: '16:9',
  duration_seconds: 18,
  metrics: [{value: '26', label: '已接入应用'}, {value: '82', label: '统一工作空间'}, {value: '1', label: '组织智能入口'}],
  scenes: [
    {code: 'scene-01', kind: 'brand', kicker: 'I-ONE AI', title: '让复杂组织\n像一个系统一样行动', subtitle: '统一业务、数据、知识与智能执行', narration: '', duration_seconds: 9},
    {code: 'scene-02', kind: 'closing', kicker: 'I-ONE AI', title: '把智能带到\n每一次行动里', subtitle: 'I-ONE AI · 组织智能运营平台', narration: '', duration_seconds: 9},
  ],
};

const metadata = ({props}) => {
  if (props.manifest.format === 'platform-film-v1') {
    return {
      durationInFrames: Math.max(1, Math.round(props.manifest.duration_seconds * 30)),
      width: 1920,
      height: 1080,
      fps: 30,
    };
  }
  const vertical = props.manifest.aspect_ratio === '9:16';
  const final = props.quality === 'final';
  return {
    durationInFrames: Math.max(1, Math.round(props.manifest.duration_seconds * 30)),
    width: vertical ? (final ? 1080 : 720) : final ? 1920 : 1280,
    height: vertical ? (final ? 1920 : 1280) : final ? 1080 : 720,
    fps: 30,
  };
};

export const VideoRoot: React.FC = () => (
  <>
    <Composition id="DealPromo" component={DealPromo} durationInFrames={1080} fps={30} width={1920} height={1080} defaultProps={{manifest: defaultManifest, quality: 'draft'}} calculateMetadata={metadata} />
    <Composition id="PlatformFilm" component={PlatformFilm} durationInFrames={540} fps={30} width={1920} height={1080} defaultProps={{manifest: platformDefault, quality: 'draft'}} calculateMetadata={metadata} />
  </>
);
