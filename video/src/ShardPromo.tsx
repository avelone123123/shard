import React from "react";
import { Audio, Sequence, interpolate, staticFile, useCurrentFrame } from "remotion";
import { SceneCTA, SceneDev, SceneDistributed, SceneHook, SceneIntro, SceneLocal, SceneSoup } from "./Scenes";
import { FONT_CSS, FONTS, PALETTE } from "./styles";
import timing from "./timing.json";
const SCENE_COMPONENTS: Record<string, React.FC> = {
  hook: SceneHook,
  intro: SceneIntro,
  local: SceneLocal,
  dev: SceneDev,
  distributed: SceneDistributed,
  soup: SceneSoup,
  cta: SceneCTA,
};
export const ShardPromo: React.FC = () => {
  const frame = useCurrentFrame();
  const pct = Math.min(100, Math.max(0, (frame / timing.totalFrames) * 100));
  const activeScene = timing.scenes.find((s) => frame >= s.startFrame && frame < s.startFrame + s.durationFrames) || timing.scenes[timing.scenes.length - 1];
  const allBeats = timing.scenes.flatMap((s) => s.beats);
  const activeBeat = allBeats.find((b) => frame >= b.startFrame && frame <= b.endFrame);
  return (
    <div style={{ width: 1080, height: 1920, backgroundColor: PALETTE.bg, backgroundImage: "radial-gradient(rgba(255,255,255,0.035) 1px, transparent 1px)", backgroundSize: "24px 24px", position: "relative", overflow: "hidden", color: PALETTE.text }}>
      <style>{FONT_CSS}</style>
      <Audio src={staticFile("audio/master_audio.mp3")} />
      {timing.scenes.map((sc) => {
        const Comp = SCENE_COMPONENTS[sc.id];
        if (!Comp) return null;
        return (
          <Sequence key={sc.id} from={sc.startFrame} durationInFrames={sc.durationFrames}>
            <FadeEnvelope durationFrames={sc.durationFrames}>
              <Comp />
            </FadeEnvelope>
          </Sequence>
        );
      })}
      <div style={{ position: "absolute", top: 68, left: 52, right: 52, zIndex: 20, display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: "rgba(26,29,36,0.86)", border: `1px solid ${PALETTE.borderStrong}`, borderRadius: 12, padding: "12px 20px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <svg width="28" height="28" viewBox="0 0 24 24">
              <rect width="24" height="24" rx="5" fill={PALETTE.accent} />
              <polygon points="12,4 19,8 19,16 12,20 5,16 5,8" fill="none" stroke="#ffffff" strokeWidth="1.8" />
              <line x1="12" y1="4" x2="12" y2="20" stroke="#ffffff" strokeWidth="1.4" />
            </svg>
            <span style={{ fontFamily: FONTS.display, fontSize: 22, fontWeight: 700, color: PALETTE.text, letterSpacing: 0.6 }}>SHARD</span>
            <span style={{ fontFamily: FONTS.mono, fontSize: 16, color: PALETTE.muted }}>{activeScene.title}</span>
          </div>
          <span style={{ fontFamily: FONTS.mono, fontSize: 16, fontWeight: 700, color: PALETTE.tealLight }}>v1.8.1</span>
        </div>
        <div style={{ width: "100%", height: 5, background: PALETTE.elevated, borderRadius: 3, overflow: "hidden" }}>
          <div style={{ width: `${pct}%`, height: "100%", background: `linear-gradient(90deg, ${PALETTE.accent}, ${PALETTE.tealLight})` }} />
        </div>
      </div>
      {activeBeat && (
        <div style={{ position: "absolute", bottom: 185, left: 52, right: 112, zIndex: 25, display: "flex", justifyContent: "center" }}>
          <div style={{ background: "rgba(16,19,24,0.92)", border: `1.5px solid ${PALETTE.tealBorder}`, borderRadius: 14, padding: "18px 26px", boxShadow: "0 16px 40px rgba(0,0,0,0.7)", maxWidth: 916 }}>
            <div style={{ fontFamily: FONTS.sans, fontSize: 33, fontWeight: 700, lineHeight: 1.3, color: PALETTE.text, textAlign: "center" }}>
              {activeBeat.text}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
const FadeEnvelope: React.FC<{ durationFrames: number; children: React.ReactNode }> = ({ durationFrames, children }) => {
  const frame = useCurrentFrame();
  const fadeIn = interpolate(frame, [0, 8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const fadeOut = interpolate(frame, [Math.max(8, durationFrames - 8), durationFrames], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return <div style={{ position: "absolute", inset: 0, opacity: Math.min(fadeIn, fadeOut) }}>{children}</div>;
};
