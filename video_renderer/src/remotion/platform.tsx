import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

const C = {
  black: '#07090d',
  ink: '#101319',
  paper: '#f4f5f1',
  white: '#ffffff',
  soft: '#d8dcd6',
  muted: '#8a918e',
  cyan: '#47d7c1',
  blue: '#5e8bff',
  amber: '#ffc857',
  coral: '#ff6b62',
  green: '#69d17d',
  line: '#2a3038',
};

const accents = {
  brand: C.cyan,
  tension: C.coral,
  'operating-system': C.cyan,
  ai: C.cyan,
  business: C.blue,
  people: C.green,
  growth: C.coral,
  collaboration: C.amber,
  insight: C.cyan,
  healthcare: C.coral,
  insurance: C.blue,
  quality: C.green,
  screening: C.coral,
  education: C.amber,
  ecosystem: C.cyan,
  trust: C.green,
  closing: C.cyan,
};

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

const reveal = (frame, fps, delay = 0) =>
  spring({frame: frame - delay, fps, config: {damping: 20, stiffness: 115, mass: 0.8}});

const sceneOpacity = (frame, frames) =>
  Math.min(
    interpolate(frame, [0, 10], [0, 1], clamp),
    interpolate(frame, [frames - 12, frames - 2], [1, 0], clamp),
  );

const Backdrop = ({accent, light = false, frame = 0}) => {
  const base = light ? C.paper : C.black;
  const line = light ? '#d5d9d3' : C.line;
  const drift = frame % 90;
  return (
    <AbsoluteFill style={{backgroundColor: base, overflow: 'hidden'}}>
      <div
        style={{
          position: 'absolute',
          inset: -120,
          opacity: light ? 0.45 : 0.55,
          transform: `translate(${drift * 0.16}px, ${drift * 0.08}px)`,
          backgroundImage: `linear-gradient(${line} 1px, transparent 1px), linear-gradient(90deg, ${line} 1px, transparent 1px)`,
          backgroundSize: '72px 72px',
        }}
      />
      <div style={{position: 'absolute', left: 74, top: 0, bottom: 0, width: 3, backgroundColor: accent}} />
      <div style={{position: 'absolute', left: 74, right: 74, bottom: 64, height: 1, backgroundColor: line}} />
    </AbsoluteFill>
  );
};

const BrandBug = ({index, total, light = false}) => (
  <>
    <div
      style={{
        position: 'absolute',
        left: 106,
        top: 46,
        display: 'flex',
        gap: 14,
        alignItems: 'center',
        color: light ? C.ink : C.white,
        fontSize: 19,
        fontWeight: 850,
      }}
    >
      <span style={{display: 'inline-block', width: 13, height: 13, backgroundColor: C.cyan}} />
      I-ONE AI
    </div>
    <div
      style={{
        position: 'absolute',
        right: 88,
        top: 46,
        color: light ? '#68706d' : C.muted,
        fontSize: 17,
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      {String(index + 1).padStart(2, '0')} / {String(total).padStart(2, '0')}
    </div>
  </>
);

const Kicker = ({children, accent}) => (
  <div style={{display: 'flex', alignItems: 'center', gap: 15, marginBottom: 24}}>
    <span style={{width: 44, height: 3, backgroundColor: accent}} />
    <span style={{fontSize: 18, fontWeight: 850, color: accent, letterSpacing: 1.5}}>{children}</span>
  </div>
);

const AppChips = ({apps = [], accent, light = false}) => (
  <div style={{display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 30}}>
    {apps.map((app, index) => (
      <div
        key={`${app}-${index}`}
        style={{
          padding: '9px 14px',
          border: `1px solid ${light ? '#cbd0ca' : '#333a43'}`,
          backgroundColor: index === 0 ? accent : light ? '#ffffff' : '#11151c',
          color: index === 0 ? C.black : light ? C.ink : C.soft,
          fontSize: 18,
          fontWeight: 700,
          borderRadius: 4,
        }}
      >
        {app}
      </div>
    ))}
  </div>
);

const Screenshot = ({src, frame, accent, compact = false}) => {
  const zoom = interpolate(frame, [0, 240], [1.025, 1.065], {...clamp, easing: Easing.out(Easing.quad)});
  const y = interpolate(frame, [0, 240], [0, -18], clamp);
  return (
    <div
      style={{
        position: 'relative',
        width: compact ? 930 : 1080,
        height: compact ? 560 : 648,
        backgroundColor: '#eef0ed',
        border: '1px solid #333a43',
        boxShadow: '0 36px 100px rgba(0,0,0,0.45)',
        overflow: 'hidden',
        borderRadius: 8,
      }}
    >
      <div style={{height: 34, backgroundColor: '#151a21', display: 'flex', alignItems: 'center', gap: 8, paddingLeft: 16}}>
        {[0, 1, 2].map((dot) => <span key={dot} style={{width: 9, height: 9, borderRadius: '50%', backgroundColor: dot === 0 ? accent : '#4a515c'}} />)}
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 34, bottom: 0, overflow: 'hidden'}}>
        <Img
          src={src}
          style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center top', transform: `translateY(${y}px) scale(${zoom})`}}
        />
      </div>
      <div style={{position: 'absolute', left: 0, top: 34, bottom: 0, width: 4, backgroundColor: accent}} />
    </div>
  );
};

const Subtitle = ({text, opacity}) => {
  if (!text) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: 280,
        right: 280,
        bottom: 25,
        textAlign: 'center',
        color: C.white,
        fontSize: 25,
        lineHeight: 1.45,
        textShadow: '0 2px 9px rgba(0,0,0,0.95)',
        opacity,
      }}
    >
      {text}
    </div>
  );
};

const BrandScene = ({scene, frame, frames, manifest}) => {
  const {fps} = useVideoConfig();
  const intro = reveal(frame, fps, 2);
  const line = reveal(frame, fps, 14);
  const opacity = sceneOpacity(frame, frames);
  return (
    <AbsoluteFill style={{color: C.white}}>
      <Backdrop accent={C.cyan} frame={frame} />
      <div style={{position: 'absolute', left: 116, top: 125, color: C.cyan, fontSize: 22, fontWeight: 850, letterSpacing: 2.2}}>I-ONE / OPERATING INTELLIGENCE</div>
      <div
        style={{
          position: 'absolute',
          left: 112,
          top: 258,
          width: 1450,
          fontSize: 105,
          fontWeight: 900,
          lineHeight: 1.08,
          letterSpacing: 0,
          whiteSpace: 'pre-line',
          opacity: intro * opacity,
          transform: `translateY(${(1 - intro) * 54}px)`,
        }}
      >
        {scene.title}
      </div>
      <div style={{position: 'absolute', left: 116, top: 565, width: 0 + line * 1120, height: 3, backgroundColor: C.cyan, opacity}} />
      <div style={{position: 'absolute', left: 116, top: 605, fontSize: 31, color: C.soft, opacity: line * opacity}}>{scene.subtitle}</div>
      <div style={{position: 'absolute', left: 116, right: 100, bottom: 102, display: 'flex', gap: 70, opacity}}>
        {manifest.metrics.map((metric) => (
          <div key={metric.label} style={{display: 'flex', alignItems: 'baseline', gap: 13}}>
            <span style={{fontSize: 62, fontWeight: 900, color: C.white}}>{metric.value}</span>
            <span style={{fontSize: 20, color: C.muted}}>{metric.label}</span>
          </div>
        ))}
      </div>
      <Subtitle text={scene.narration} opacity={opacity} />
    </AbsoluteFill>
  );
};

const TensionScene = ({scene, frame, frames, index, total}) => {
  const {fps} = useVideoConfig();
  const opacity = sceneOpacity(frame, frames);
  return (
    <AbsoluteFill style={{color: C.white}}>
      <Backdrop accent={C.coral} frame={frame} />
      <BrandBug index={index} total={total} />
      <div style={{position: 'absolute', left: 112, top: 150, right: 90}}>
        <Kicker accent={C.coral}>{scene.kicker}</Kicker>
        <div style={{fontSize: 76, lineHeight: 1.12, fontWeight: 900, whiteSpace: 'pre-line', opacity}}>{scene.title}</div>
      </div>
      <div style={{position: 'absolute', left: 112, right: 90, top: 520, display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16}}>
        {scene.bullets.map((bullet, bulletIndex) => {
          const r = reveal(frame, fps, 18 + bulletIndex * 5);
          return (
            <div
              key={bullet}
              style={{
                height: 250,
                padding: '30px 30px',
                borderTop: `5px solid ${bulletIndex === 2 ? C.amber : C.coral}`,
                backgroundColor: '#11151c',
                borderLeft: '1px solid #303640',
                opacity: r * opacity,
                transform: `translateY(${(1 - r) * 32}px)`,
              }}
            >
              <div style={{fontSize: 18, color: C.muted}}>0{bulletIndex + 1}</div>
              <div style={{fontSize: 42, fontWeight: 850, marginTop: 84}}>{bullet}</div>
            </div>
          );
        })}
      </div>
      <Subtitle text={scene.narration} opacity={opacity} />
    </AbsoluteFill>
  );
};

const OperatingSystemScene = ({scene, frame, frames, index, total}) => {
  const {fps} = useVideoConfig();
  const opacity = sceneOpacity(frame, frames);
  return (
    <AbsoluteFill style={{color: C.white}}>
      <Backdrop accent={C.cyan} frame={frame} />
      <BrandBug index={index} total={total} />
      <div style={{position: 'absolute', left: 112, top: 170, width: 760}}>
        <Kicker accent={C.cyan}>{scene.kicker}</Kicker>
        <div style={{fontSize: 72, lineHeight: 1.13, fontWeight: 900, whiteSpace: 'pre-line', opacity}}>{scene.title}</div>
        <div style={{fontSize: 27, lineHeight: 1.55, color: C.soft, marginTop: 28, opacity}}>{scene.subtitle}</div>
      </div>
      <div style={{position: 'absolute', left: 980, top: 170, width: 780, height: 690, perspective: 1100}}>
        {scene.layers.map((layer, layerIndex) => {
          const r = reveal(frame, fps, 10 + layerIndex * 6);
          return (
            <div
              key={layer}
              style={{
                position: 'absolute',
                left: layerIndex * 22,
                right: 0,
                top: layerIndex * 132,
                height: 112,
                display: 'flex',
                alignItems: 'center',
                padding: '0 28px',
                backgroundColor: layerIndex === 3 ? C.cyan : '#11161d',
                border: `1px solid ${layerIndex === 3 ? C.cyan : '#333a43'}`,
                color: layerIndex === 3 ? C.black : C.white,
                fontSize: 28,
                fontWeight: 800,
                opacity: r * opacity,
                transform: `rotateX(4deg) rotateY(-8deg) translateX(${(1 - r) * 90}px)`,
                boxShadow: '0 28px 60px rgba(0,0,0,0.34)',
              }}
            >
              <span style={{width: 48, fontSize: 18, opacity: 0.65}}>0{layerIndex + 1}</span>{layer}
            </div>
          );
        })}
      </div>
      <Subtitle text={scene.narration} opacity={opacity} />
    </AbsoluteFill>
  );
};

const AICapabilityScene = ({scene, frame, frames, index, total}) => {
  const {fps} = useVideoConfig();
  const opacity = sceneOpacity(frame, frames);
  return (
    <AbsoluteFill style={{color: C.white}}>
      <Backdrop accent={C.cyan} frame={frame} />
      <BrandBug index={index} total={total} />
      <div style={{position: 'absolute', left: 112, top: 138, width: 720}}>
        <Kicker accent={C.cyan}>{scene.kicker}</Kicker>
        <div style={{fontSize: 70, lineHeight: 1.12, fontWeight: 900, whiteSpace: 'pre-line'}}>{scene.title}</div>
        <div style={{marginTop: 34, padding: '21px 24px', backgroundColor: '#121820', borderLeft: `4px solid ${C.cyan}`, fontSize: 23, lineHeight: 1.5}}>{scene.command}</div>
        <div style={{marginTop: 22, display: 'grid', gap: 10}}>
          {scene.actions.map((action, actionIndex) => {
            const r = reveal(frame, fps, 22 + actionIndex * 6);
            return (
              <div key={action} style={{display: 'flex', alignItems: 'center', gap: 16, opacity: r}}>
                <div style={{width: 28, height: 28, backgroundColor: actionIndex === scene.actions.length - 1 ? C.cyan : '#202833', display: 'flex', alignItems: 'center', justifyContent: 'center', color: actionIndex === scene.actions.length - 1 ? C.black : C.soft, fontSize: 14, fontWeight: 900}}>{actionIndex + 1}</div>
                <div style={{fontSize: 22, color: C.soft}}>{action}</div>
              </div>
            );
          })}
        </div>
      </div>
      <div style={{position: 'absolute', right: 86, top: 164, opacity}}>
        <Screenshot src={scene.asset_data_uri} frame={frame} accent={C.cyan} compact />
      </div>
      <Subtitle text={scene.narration} opacity={opacity} />
    </AbsoluteFill>
  );
};

const ProductScene = ({scene, frame, frames, index, total}) => {
  const {fps} = useVideoConfig();
  const accent = accents[scene.kind] || C.cyan;
  const opacity = sceneOpacity(frame, frames);
  const titleIn = reveal(frame, fps, 2);
  return (
    <AbsoluteFill style={{color: C.white}}>
      <Backdrop accent={accent} frame={frame} />
      <BrandBug index={index} total={total} />
      <div style={{position: 'absolute', left: 112, top: 145, width: 650, zIndex: 3}}>
        <Kicker accent={accent}>{scene.kicker}</Kicker>
        <div style={{fontSize: 66, lineHeight: 1.13, fontWeight: 900, whiteSpace: 'pre-line', opacity: titleIn}}>{scene.title}</div>
        <div style={{fontSize: 25, lineHeight: 1.55, color: C.soft, marginTop: 24, maxWidth: 600}}>{scene.subtitle}</div>
        <AppChips apps={scene.apps} accent={accent} />
      </div>
      {scene.asset_data_uri ? (
        <div style={{position: 'absolute', right: 72, top: 186, opacity, transform: `translateX(${(1 - titleIn) * 80}px)`}}>
          <Screenshot src={scene.asset_data_uri} frame={frame} accent={accent} compact />
        </div>
      ) : null}
      <Subtitle text={scene.narration} opacity={opacity} />
    </AbsoluteFill>
  );
};

const InsightScene = ({scene, frame, frames, index, total}) => {
  const {fps} = useVideoConfig();
  const opacity = sceneOpacity(frame, frames);
  const values = [36, 58, 46, 73, 67, 89, 82, 96];
  return (
    <AbsoluteFill style={{color: C.white}}>
      <Backdrop accent={C.cyan} frame={frame} />
      <BrandBug index={index} total={total} />
      <div style={{position: 'absolute', left: 112, top: 160, width: 710}}>
        <Kicker accent={C.cyan}>{scene.kicker}</Kicker>
        <div style={{fontSize: 72, lineHeight: 1.08, fontWeight: 900, whiteSpace: 'pre-line'}}>{scene.title}</div>
        <div style={{fontSize: 26, color: C.soft, marginTop: 26}}>{scene.subtitle}</div>
        <AppChips apps={scene.apps} accent={C.cyan} />
      </div>
      <div style={{position: 'absolute', right: 90, top: 160, width: 900, height: 650, border: '1px solid #303640', backgroundColor: '#0e1319', padding: 38}}>
        <div style={{display: 'flex', justifyContent: 'space-between', color: C.muted, fontSize: 18}}><span>组织经营指数</span><span>实时更新</span></div>
        <div style={{position: 'absolute', left: 48, right: 48, bottom: 70, height: 450, display: 'flex', alignItems: 'flex-end', gap: 24}}>
          {values.map((value, valueIndex) => {
            const r = reveal(frame, fps, 12 + valueIndex * 3);
            return (
              <div key={valueIndex} style={{flex: 1, height: `${value * r}%`, backgroundColor: valueIndex === values.length - 1 ? C.cyan : '#23313a', position: 'relative'}}>
                <span style={{position: 'absolute', top: -28, left: 0, right: 0, textAlign: 'center', fontSize: 16, color: valueIndex === values.length - 1 ? C.cyan : C.muted}}>{value}</span>
              </div>
            );
          })}
        </div>
        <div style={{position: 'absolute', left: 48, bottom: 28, fontSize: 18, color: C.green}}>+18.6% 运营协同效率</div>
      </div>
      <Subtitle text={scene.narration} opacity={opacity} />
    </AbsoluteFill>
  );
};

const TrustScene = ({scene, frame, frames, index, total}) => {
  const {fps} = useVideoConfig();
  const opacity = sceneOpacity(frame, frames);
  return (
    <AbsoluteFill style={{color: C.ink}}>
      <Backdrop accent={C.green} light frame={frame} />
      <BrandBug index={index} total={total} light />
      <div style={{position: 'absolute', left: 112, top: 165, width: 920}}>
        <Kicker accent={'#178a4d'}>{scene.kicker}</Kicker>
        <div style={{fontSize: 72, lineHeight: 1.12, fontWeight: 900, whiteSpace: 'pre-line'}}>{scene.title}</div>
        <div style={{fontSize: 27, color: '#5f6763', marginTop: 26}}>{scene.subtitle}</div>
      </div>
      <div style={{position: 'absolute', left: 112, right: 90, bottom: 145, display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12}}>
        {scene.bullets.map((item, itemIndex) => {
          const r = reveal(frame, fps, 12 + itemIndex * 5);
          return (
            <div key={item} style={{height: 175, padding: 24, backgroundColor: C.white, borderTop: `5px solid ${C.green}`, opacity: r * opacity, transform: `translateY(${(1 - r) * 25}px)`}}>
              <div style={{fontSize: 16, color: '#818985'}}>TRUST 0{itemIndex + 1}</div>
              <div style={{fontSize: 31, fontWeight: 850, marginTop: 68}}>{item}</div>
            </div>
          );
        })}
      </div>
      <Subtitle text={scene.narration} opacity={opacity} />
    </AbsoluteFill>
  );
};

const ClosingScene = ({scene, frame, frames}) => {
  const {fps} = useVideoConfig();
  const opacity = sceneOpacity(frame, frames);
  const r = reveal(frame, fps, 4);
  return (
    <AbsoluteFill style={{color: C.white}}>
      <Backdrop accent={C.cyan} frame={frame} />
      <div style={{position: 'absolute', left: 112, right: 90, top: 190, bottom: 150, display: 'flex', flexDirection: 'column', justifyContent: 'center'}}>
        <div style={{fontSize: 24, color: C.cyan, fontWeight: 900, letterSpacing: 3}}>I-ONE AI</div>
        <div style={{marginTop: 46, fontSize: 94, lineHeight: 1.08, fontWeight: 900, whiteSpace: 'pre-line', opacity: r, transform: `translateY(${(1 - r) * 45}px)`}}>{scene.title}</div>
        <div style={{marginTop: 44, paddingTop: 28, borderTop: `3px solid ${C.cyan}`, fontSize: 31, color: C.soft, width: 1000}}>{scene.subtitle}</div>
        <div style={{position: 'absolute', right: 0, bottom: 20, width: 340, height: 340, border: `1px solid ${C.cyan}`, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
          <div style={{fontSize: 68, fontWeight: 950}}>I·ONE</div>
        </div>
      </div>
      <Subtitle text={scene.narration} opacity={opacity} />
    </AbsoluteFill>
  );
};

const Scene = ({scene, frame, frames, index, total, manifest}) => {
  if (scene.kind === 'brand') return <BrandScene scene={scene} frame={frame} frames={frames} manifest={manifest} />;
  if (scene.kind === 'tension') return <TensionScene scene={scene} frame={frame} frames={frames} index={index} total={total} />;
  if (scene.kind === 'operating-system') return <OperatingSystemScene scene={scene} frame={frame} frames={frames} index={index} total={total} />;
  if (scene.kind === 'ai') return <AICapabilityScene scene={scene} frame={frame} frames={frames} index={index} total={total} />;
  if (scene.kind === 'insight') return <InsightScene scene={scene} frame={frame} frames={frames} index={index} total={total} />;
  if (scene.kind === 'trust') return <TrustScene scene={scene} frame={frame} frames={frames} index={index} total={total} />;
  if (scene.kind === 'closing') return <ClosingScene scene={scene} frame={frame} frames={frames} />;
  return <ProductScene scene={scene} frame={frame} frames={frames} index={index} total={total} />;
};

export const PlatformFilm = ({manifest}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  let cursor = 0;
  let activeIndex = 0;
  let activeStart = 0;
  for (let index = 0; index < manifest.scenes.length; index += 1) {
    const length = Math.round(manifest.scenes[index].duration_seconds * fps);
    if (frame < cursor + length || index === manifest.scenes.length - 1) {
      activeIndex = index;
      activeStart = cursor;
      break;
    }
    cursor += length;
  }
  const active = manifest.scenes[activeIndex];
  const activeFrames = Math.round(active.duration_seconds * fps);
  let audioCursor = 0;
  return (
    <AbsoluteFill style={{fontFamily: 'Noto Sans CJK SC, Noto Sans SC, Microsoft YaHei, sans-serif'}}>
      {manifest.music_data_uri ? <Audio src={manifest.music_data_uri} volume={0.18} /> : null}
      {manifest.scenes.map((scene) => {
        const from = audioCursor;
        const duration = Math.round(scene.duration_seconds * fps);
        audioCursor += duration;
        return scene.narration_audio_data_uri ? (
          <Sequence key={`${scene.code}-audio`} from={from} durationInFrames={duration} name={`${scene.code} narration`}>
            <Audio src={scene.narration_audio_data_uri} volume={1} />
          </Sequence>
        ) : null;
      })}
      <Scene
        scene={active}
        frame={frame - activeStart}
        frames={activeFrames}
        index={activeIndex}
        total={manifest.scenes.length}
        manifest={manifest}
      />
      <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 5, backgroundColor: '#262d35'}}>
        <div style={{height: '100%', width: `${Math.min(100, (frame / Math.max(1, manifest.duration_seconds * fps)) * 100)}%`, backgroundColor: C.cyan}} />
      </div>
    </AbsoluteFill>
  );
};
