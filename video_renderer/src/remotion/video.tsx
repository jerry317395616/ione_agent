import React from 'react';
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

const palette = {
  ink: '#111827',
  muted: '#667085',
  white: '#ffffff',
  surface: '#f7f9fc',
  line: '#d8dee8',
  blue: '#2563eb',
  teal: '#0f8f7f',
  gold: '#d99a13',
  rose: '#d8587e',
  dark: '#0b1220',
};

const sceneAccent = {
  cover: palette.teal,
  context: palette.blue,
  challenge: palette.rose,
  solution: palette.teal,
  capability: palette.blue,
  roadmap: palette.gold,
  value: palette.teal,
  closing: palette.gold,
};

const titleSize = (title, vertical) => {
  if (title.length > 28) return vertical ? 40 : 38;
  if (title.length > 20) return vertical ? 44 : 42;
  if (title.length > 14) return vertical ? 48 : 46;
  if (title.length > 9) return vertical ? 54 : 52;
  return vertical ? 66 : 60;
};

const Scene = ({scene, manifest, localFrame, sceneFrames, index}) => {
  const {fps, width, height} = useVideoConfig();
  const vertical = height > width;
  const entrance = spring({frame: localFrame, fps, config: {damping: 18, stiffness: 120}});
  const exit = interpolate(localFrame, [sceneFrames - 12, sceneFrames], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const opacity = Math.min(entrance, exit);
  const accent = sceneAccent[scene.kind] || palette.blue;
  const dark = scene.kind === 'cover' || scene.kind === 'closing';
  const foreground = dark ? palette.white : palette.ink;
  const muted = dark ? '#cbd5e1' : palette.muted;
  const outer = vertical ? 64 : 96;
  const hasAsset = Boolean(scene.asset_data_uri);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: dark ? palette.dark : palette.surface,
        color: foreground,
        fontFamily: 'Noto Sans CJK SC, Noto Sans SC, Microsoft YaHei, sans-serif',
      }}
    >
      <div style={{position: 'absolute', inset: 0, overflow: 'hidden'}}>
        <div
          style={{
            position: 'absolute',
            width: vertical ? 480 : 700,
            height: vertical ? 480 : 700,
            right: vertical ? -260 : -240,
            top: vertical ? 80 : -260,
            borderRadius: '50%',
            border: `2px solid ${accent}55`,
          }}
        />
        <div
          style={{
            position: 'absolute',
            width: vertical ? 300 : 460,
            height: vertical ? 300 : 460,
            right: vertical ? -160 : -90,
            bottom: vertical ? 160 : -260,
            borderRadius: '50%',
            border: `24px solid ${accent}18`,
          }}
        />
      </div>

      <div
        style={{
          position: 'relative',
          display: 'flex',
          flexDirection: vertical ? 'column' : 'row',
          gap: vertical ? 48 : 72,
          height: '100%',
          padding: vertical ? `${outer + 40}px ${outer}px 150px` : `${outer}px ${outer}px 110px`,
          opacity,
          transform: `translateY(${(1 - entrance) * 36}px)`,
        }}
      >
        <div style={{display: 'flex', flex: 1.2, flexDirection: 'column', justifyContent: 'center'}}>
          <div style={{display: 'flex', alignItems: 'center', gap: 18, marginBottom: 34}}>
            <div style={{width: 64, height: 8, borderRadius: 4, backgroundColor: accent}} />
            <div style={{fontSize: vertical ? 24 : 22, fontWeight: 700, color: accent, letterSpacing: 0}}>
              {scene.kind === 'cover' ? '客户解决方案' : `0${index + 1}`.slice(-2)}
            </div>
          </div>
          <div
            style={{
              fontSize: titleSize(scene.title, vertical),
              fontWeight: 800,
              lineHeight: 1.16,
              letterSpacing: 0,
              maxWidth: vertical ? '100%' : 950,
            }}
          >
            {scene.title}
          </div>
          {scene.subtitle ? (
            <div style={{fontSize: vertical ? 31 : 29, lineHeight: 1.5, color: muted, marginTop: 28}}>
              {scene.subtitle}
            </div>
          ) : null}
          {scene.bullets?.length ? (
            <div style={{display: 'grid', gap: vertical ? 22 : 18, marginTop: 40}}>
              {scene.bullets.map((bullet, bulletIndex) => (
                <div
                  key={`${scene.code}-bullet-${bulletIndex}`}
                  style={{display: 'flex', alignItems: 'flex-start', gap: 18, fontSize: vertical ? 29 : 26, lineHeight: 1.45}}
                >
                  <div style={{width: 12, height: 12, borderRadius: '50%', backgroundColor: accent, marginTop: 13, flex: '0 0 auto'}} />
                  <div>{bullet}</div>
                </div>
              ))}
            </div>
          ) : null}
        </div>

        {hasAsset ? (
          <div
            style={{
              flex: vertical ? '0 0 39%' : 0.8,
              minHeight: vertical ? 560 : 0,
              borderRadius: 8,
              overflow: 'hidden',
              border: `1px solid ${dark ? '#334155' : palette.line}`,
              boxShadow: '0 28px 80px rgba(15, 23, 42, 0.18)',
              alignSelf: 'stretch',
            }}
          >
            <Img src={scene.asset_data_uri} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          </div>
        ) : (
          <div
            style={{
              flex: vertical ? '0 0 28%' : 0.55,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              minHeight: vertical ? 400 : 0,
            }}
          >
            <div
              style={{
                width: vertical ? 330 : 360,
                height: vertical ? 330 : 360,
                borderRadius: 8,
                backgroundColor: dark ? '#111c31' : palette.white,
                border: `1px solid ${dark ? '#334155' : palette.line}`,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 28px 80px rgba(15, 23, 42, 0.12)',
              }}
            >
              <div style={{fontSize: 72, fontWeight: 900, color: accent, letterSpacing: 0}}>I-ONE</div>
              <div style={{fontSize: 26, fontWeight: 700, color: muted, marginTop: 10}}>AI</div>
            </div>
          </div>
        )}
      </div>

      {scene.narration ? (
        <div
          style={{
            position: 'absolute',
            left: outer,
            right: outer,
            bottom: vertical ? 92 : 55,
            textAlign: 'center',
            fontSize: vertical ? 25 : 22,
            lineHeight: 1.4,
            color: dark ? '#e2e8f0' : palette.ink,
            backgroundColor: dark ? '#111827dd' : '#ffffffee',
            border: `1px solid ${dark ? '#334155' : palette.line}`,
            padding: '14px 22px',
            borderRadius: 8,
            opacity,
          }}
        >
          {scene.narration}
        </div>
      ) : null}

      <div style={{position: 'absolute', left: outer, top: 38, fontSize: 18, fontWeight: 700, color: muted}}>
        {manifest.customer || manifest.brand}
      </div>
      <div style={{position: 'absolute', right: outer, top: 38, fontSize: 18, fontWeight: 700, color: muted}}>
        {manifest.brand}
      </div>
    </AbsoluteFill>
  );
};

export const DealPromo = ({manifest}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  let startFrame = 0;
  let active = manifest.scenes[0];
  let activeIndex = 0;
  let sceneFrames = Math.round(active.duration_seconds * fps);
  for (let index = 0; index < manifest.scenes.length; index += 1) {
    const candidateFrames = Math.round(manifest.scenes[index].duration_seconds * fps);
    if (frame < startFrame + candidateFrames || index === manifest.scenes.length - 1) {
      active = manifest.scenes[index];
      activeIndex = index;
      sceneFrames = candidateFrames;
      break;
    }
    startFrame += candidateFrames;
  }
  const totalFrames = Math.max(1, Math.round(manifest.duration_seconds * fps));
  const progress = Math.min(1, Math.max(0, frame / totalFrames));
  return (
    <AbsoluteFill>
      <Scene scene={active} manifest={manifest} localFrame={frame - startFrame} sceneFrames={sceneFrames} index={activeIndex} />
      <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 8, backgroundColor: '#d8dee866'}}>
        <div style={{height: '100%', width: `${progress * 100}%`, backgroundColor: palette.teal}} />
      </div>
    </AbsoluteFill>
  );
};
