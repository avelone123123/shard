import React from "react";
import { Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { FONTS, PALETTE } from "./styles";
export const ShardHexLogo: React.FC<{ size?: number; strokeProgress?: number }> = ({ size = 120, strokeProgress = 1 }) => {
  const perim = 240;
  const dash = Math.max(0, (1 - strokeProgress) * perim);
  return (
    <svg width={size} height={size} viewBox="0 0 100 100">
      <rect x="6" y="6" width="88" height="88" rx="20" fill={PALETTE.tealSoft} stroke={PALETTE.tealBorder} strokeWidth="2.5" />
      <polygon
        points="50,18 76,33 76,67 50,82 24,67 24,33"
        fill="none"
        stroke={PALETTE.tealLight}
        strokeWidth="3.2"
        strokeLinejoin="round"
        strokeDasharray={perim}
        strokeDashoffset={dash}
      />
      <line x1="50" y1="18" x2="50" y2="82" stroke={PALETTE.teal} strokeWidth="2.4" opacity={strokeProgress} />
      <line x1="24" y1="33" x2="76" y2="67" stroke={PALETTE.tealLight} strokeWidth="1.6" opacity={strokeProgress * 0.65} />
      <line x1="76" y1="33" x2="24" y2="67" stroke={PALETTE.tealLight} strokeWidth="1.6" opacity={strokeProgress * 0.65} />
      <circle cx="50" cy="50" r="6" fill={PALETTE.tealLight} opacity={strokeProgress} />
    </svg>
  );
};
export const SceneHook: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pop = spring({ frame, fps, config: { damping: 14, stiffness: 120 } });
  const strike = interpolate(frame, [20, 48], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const bgScale = interpolate(frame, [0, 110], [1.02, 1.1]);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", justifyContent: "center", padding: "0 76px" }}>
      <Img src={staticFile("bg/gpu_blade.jpg")} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", opacity: 0.42, transform: `scale(${bgScale})` }} />
      <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg, rgba(22,24,29,0.72) 0%, rgba(22,24,29,0.88) 55%, rgba(16,19,24,0.98) 100%)" }} />
      <div style={{ position: "relative", zIndex: 2, transform: `translateY(${(1 - pop) * 44}px)`, opacity: pop }}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 10, padding: "10px 18px", borderRadius: 10, background: PALETTE.warningBg, border: `1px solid ${PALETTE.warningBorder}`, fontFamily: FONTS.mono, fontSize: 21, color: PALETTE.warning, marginBottom: 34, fontWeight: 700 }}>
          <span>CLOUD LOCK-IN</span>
          <span style={{ opacity: 0.6 }}>/</span>
          <span>RATE LIMITS</span>
          <span style={{ opacity: 0.6 }}>/</span>
          <span>ZERO CONTROL</span>
        </div>
        <div style={{ fontFamily: FONTS.display, fontSize: 84, lineHeight: 1.04, fontWeight: 700, color: PALETTE.text, letterSpacing: -2 }}>
          AI shouldn't live behind <span style={{ color: PALETTE.tealLight, position: "relative", display: "inline-block" }}>someone else's<span style={{ position: "absolute", left: 0, right: 0, bottom: 6, height: 5, background: PALETTE.teal, transform: `scaleX(${strike})`, transformOrigin: "left" }} /></span> servers.
        </div>
        <div style={{ marginTop: 42, display: "flex", gap: 16, flexWrap: "wrap" }}>
          {["$ PER-TOKEN FEES", "REMOTE TELEMETRY", "API QUOTAS"].map((item, i) => (
            <div key={i} style={{ padding: "12px 20px", borderRadius: 10, background: "rgba(29,33,41,0.92)", border: `1px solid ${PALETTE.borderStrong}`, fontFamily: FONTS.mono, fontSize: 20, color: PALETTE.muted, textDecoration: strike > 0.5 ? "line-through" : "none", opacity: interpolate(frame, [28 + i * 6, 46 + i * 6], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) }}>
              {item}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
export const SceneIntro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s1 = spring({ frame, fps, config: { damping: 14, stiffness: 110 } });
  const stroke = interpolate(frame, [6, 45], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const chipsIn = interpolate(frame, [42, 72], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", padding: "0 72px", textAlign: "center" }}>
      <Img src={staticFile("bg/fiber_monolith.jpg")} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", opacity: 0.34, transform: `scale(${interpolate(frame, [0, 180], [1.0, 1.07])})` }} />
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(circle at 50% 42%, rgba(15,118,110,0.22) 0%, rgba(22,24,29,0.94) 68%)" }} />
      <div style={{ position: "relative", zIndex: 2, display: "flex", flexDirection: "column", alignItems: "center" }}>
        <div style={{ transform: `scale(${0.75 + s1 * 0.25})`, opacity: s1 }}>
          <ShardHexLogo size={168} strokeProgress={stroke} />
        </div>
        <div style={{ marginTop: 28, padding: "8px 18px", borderRadius: 8, background: PALETTE.tealSoft, border: `1px solid ${PALETTE.tealBorder}`, fontFamily: FONTS.mono, fontSize: 20, fontWeight: 700, color: PALETTE.tealLight, opacity: s1 }}>
          SHARD ENGINE v1.8.1 // MIT OPEN SOURCE
        </div>
        <div style={{ fontFamily: FONTS.display, fontSize: 120, fontWeight: 700, color: PALETTE.text, letterSpacing: -3, marginTop: 18, lineHeight: 1, opacity: s1 }}>
          SHARD
        </div>
        <div style={{ fontFamily: FONTS.sans, fontSize: 38, fontWeight: 600, color: PALETTE.tealLight, marginTop: 18, opacity: s1 }}>
          Your Local AI Infrastructure
        </div>
        <div style={{ marginTop: 42, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, width: "100%", maxWidth: 820, opacity: chipsIn, transform: `translateY(${(1 - chipsIn) * 24}px)` }}>
          {[
            ["PRE-FLIGHT FIT", "VRAM / RAM / Disk Math"],
            ["DUAL ENGINE", "GGUF & PyTorch SafeTensors"],
            ["API PARITY", "OpenAI + Anthropic SSE"],
            ["DISTRIBUTED", "LAN Pool + SOUP Training"],
          ].map(([k, v], i) => (
            <div key={i} style={{ background: "rgba(29,33,41,0.92)", border: `1px solid ${PALETTE.borderStrong}`, borderRadius: 12, padding: "18px 22px", textAlign: "left" }}>
              <div style={{ fontFamily: FONTS.mono, fontSize: 16, color: PALETTE.tealLight, fontWeight: 700 }}>{k}</div>
              <div style={{ fontFamily: FONTS.sans, fontSize: 24, color: PALETTE.text, fontWeight: 600, marginTop: 6 }}>{v}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
export const SceneLocal: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sIn = spring({ frame, fps, config: { damping: 16, stiffness: 100 } });
  const swap = interpolate(frame, [95, 118], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const cliZoom = interpolate(frame, [0, 115], [1.0, 1.04]);
  const uiZoom = interpolate(frame, [95, 240], [1.04, 1.0]);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", justifyContent: "flex-start", padding: "190px 52px 0 52px" }}>
      <div style={{ opacity: sIn, transform: `translateY(${(1 - sIn) * 26}px)` }}>
        <div style={{ fontFamily: FONTS.mono, fontSize: 20, color: PALETTE.tealLight, fontWeight: 700, letterSpacing: 1 }}>
          03 // HARDWARE-FIRST RUNTIME
        </div>
        <div style={{ fontFamily: FONTS.display, fontSize: 60, fontWeight: 700, color: PALETTE.text, lineHeight: 1.08, marginTop: 10 }}>
          Know It Fits Before You Download.
        </div>
      </div>
      <div style={{ position: "relative", width: "100%", height: 840, marginTop: 34 }}>
        <div style={{ position: "absolute", inset: 0, opacity: 1 - swap, transform: `scale(${cliZoom})`, display: "flex", flexDirection: "column", justifyContent: "center" }}>
          <div style={{ borderRadius: 16, overflow: "hidden", border: `2px solid ${PALETTE.tealBorder}`, boxShadow: "0 30px 70px rgba(0,0,0,0.65)", background: PALETTE.terminal }}>
            <Img src={staticFile("shots/real-cli-fit.png")} style={{ width: "100%", display: "block" }} />
          </div>
          <div style={{ marginTop: 22, display: "flex", gap: 12, justifyContent: "center" }}>
            {["LAUNCH OK", "Q4_K_M · 2.1 GB", "999/36 GPU LAYERS", "~27.0 TOK/S"].map((b, i) => (
              <div key={i} style={{ background: PALETTE.tealSoft, border: `1px solid ${PALETTE.tealBorder}`, color: PALETTE.tealLight, fontFamily: FONTS.mono, fontSize: 18, fontWeight: 700, padding: "10px 14px", borderRadius: 8 }}>
                {b}
              </div>
            ))}
          </div>
        </div>
        <div style={{ position: "absolute", inset: 0, opacity: swap, transform: `scale(${uiZoom})`, display: "flex", flexDirection: "column", justifyContent: "center" }}>
          <div style={{ borderRadius: 16, overflow: "hidden", border: `2px solid ${PALETTE.borderStrong}`, boxShadow: "0 30px 70px rgba(0,0,0,0.7)", background: PALETTE.surface }}>
            <Img src={staticFile("shots/real-dashboard-overview.png")} style={{ width: "100%", display: "block" }} />
          </div>
          <div style={{ marginTop: 22, display: "flex", gap: 12, justifyContent: "center" }}>
            {["MODERN SLATE UI", "LIVE GPU TELEMETRY", "107+ CATALOG MODELS"].map((b, i) => (
              <div key={i} style={{ background: PALETTE.surface, border: `1px solid ${PALETTE.borderStrong}`, color: PALETTE.text, fontFamily: FONTS.mono, fontSize: 18, fontWeight: 700, padding: "10px 16px", borderRadius: 8 }}>
                {b}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
export const SceneDev: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const cardIn = spring({ frame, fps, config: { damping: 15, stiffness: 110 } });
  const typedChars = Math.floor(interpolate(frame, [8, 95], [0, 74], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }));
  const cmdFull = "shard doctor && shard fit Qwen/Qwen2.5-Coder-3B-Instruct && shard start";
  const showCode = interpolate(frame, [260, 290], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", inset: 0, padding: "185px 52px 0 52px", display: "flex", flexDirection: "column" }}>
      <div style={{ opacity: cardIn, transform: `translateY(${(1 - cardIn) * 24}px)` }}>
        <div style={{ fontFamily: FONTS.mono, fontSize: 20, color: PALETTE.tealLight, fontWeight: 700, letterSpacing: 1 }}>
          04 // DEVELOPER INFRASTRUCTURE
        </div>
        <div style={{ fontFamily: FONTS.display, fontSize: 58, fontWeight: 700, color: PALETTE.text, lineHeight: 1.08, marginTop: 10 }}>
          CLI + Dual Runtime + Drop-In API
        </div>
      </div>
      <div style={{ marginTop: 26, background: PALETTE.terminal, border: `1px solid ${PALETTE.borderStrong}`, borderRadius: 14, overflow: "hidden", boxShadow: "0 20px 50px rgba(0,0,0,0.5)" }}>
        <div style={{ background: PALETTE.chrome, padding: "12px 18px", borderBottom: `1px solid ${PALETTE.border}`, display: "flex", justifyContent: "space-between", fontFamily: FONTS.mono, fontSize: 16, color: PALETTE.muted }}>
          <span>terminal // shard-engine</span>
          <span style={{ color: PALETTE.tealLight }}>FastAPI :8000</span>
        </div>
        <div style={{ padding: "20px 22px", fontFamily: FONTS.mono, fontSize: 22, color: PALETTE.tealLight, lineHeight: 1.45, minHeight: 98 }}>
          <span style={{ color: PALETTE.success }}>$ </span>
          {cmdFull.slice(0, typedChars)}
          <span style={{ opacity: Math.floor(frame / 10) % 2 === 0 ? 1 : 0 }}>_</span>
        </div>
      </div>
      <div style={{ position: "relative", height: 600, marginTop: 24 }}>
        <div style={{ position: "absolute", inset: 0, opacity: 1 - showCode }}>
          <div style={{ borderRadius: 14, overflow: "hidden", border: `1px solid ${PALETTE.tealBorder}`, boxShadow: "0 24px 60px rgba(0,0,0,0.65)" }}>
            <Img src={staticFile("shots/real-dashboard-connect.png")} style={{ width: "100%", display: "block" }} />
          </div>
          <div style={{ marginTop: 22, display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
            {[
              ["GGUF / llama.cpp", "-ngl GPU offload"],
              ["PyTorch HF", "SafeTensors + LoRA"],
              ["C++20 / MASM", "98 AVX2 Kernels"],
            ].map(([t, s], idx) => (
              <div key={idx} style={{ background: PALETTE.surface, border: `1px solid ${PALETTE.borderStrong}`, borderRadius: 10, padding: "14px 16px" }}>
                <div style={{ fontFamily: FONTS.mono, fontSize: 18, color: PALETTE.tealLight, fontWeight: 700 }}>{t}</div>
                <div style={{ fontFamily: FONTS.sans, fontSize: 17, color: PALETTE.textSec, marginTop: 4 }}>{s}</div>
              </div>
            ))}
          </div>
        </div>
        <div style={{ position: "absolute", inset: 0, opacity: showCode, transform: `translateY(${(1 - showCode) * 20}px)` }}>
          <div style={{ background: PALETTE.terminal, border: `2px solid ${PALETTE.tealBorder}`, borderRadius: 14, overflow: "hidden", boxShadow: "0 26px 64px rgba(0,0,0,0.7)" }}>
            <div style={{ background: PALETTE.chrome, padding: "14px 20px", borderBottom: `1px solid ${PALETTE.border}`, display: "flex", justifyContent: "space-between", fontFamily: FONTS.mono, fontSize: 18 }}>
              <span style={{ color: PALETTE.text }}>agent_client.py</span>
              <span style={{ color: PALETTE.tealLight }}>OpenAI + Anthropic APIs</span>
            </div>
            <pre style={{ margin: 0, padding: "26px 24px", fontFamily: FONTS.mono, fontSize: 22, lineHeight: 1.55, color: PALETTE.text }}>
              <span style={{ color: PALETTE.info }}>from</span> openai <span style={{ color: PALETTE.info }}>import</span> OpenAI{"\n\n"}
              client = OpenAI({"\n"}
              {"  "}base_url=<span style={{ color: PALETTE.tealLight }}>"http://localhost:8000/v1"</span>,{"\n"}
              {"  "}api_key=<span style={{ color: PALETTE.tealLight }}>"shard-local"</span>{"\n"}
              ){"\n\n"}
              res = client.chat.completions.create({"\n"}
              {"  "}model=<span style={{ color: PALETTE.tealLight }}>"Qwen/Qwen2.5-Coder-3B"</span>,{"\n"}
              {"  "}messages=[&#123;<span style={{ color: PALETTE.tealLight }}>"role"</span>: <span style={{ color: PALETTE.tealLight }}>"user"</span>, <span style={{ color: PALETTE.tealLight }}>"content"</span>: <span style={{ color: PALETTE.tealLight }}>"Hello"</span>&#125;],{"\n"}
              {"  "}stream=<span style={{ color: PALETTE.success }}>True</span>{"\n"}
              )
            </pre>
          </div>
          <div style={{ marginTop: 20, display: "flex", gap: 12, justifyContent: "center" }}>
            {["/v1/chat/completions", "/v1/messages", "/v1/models"].map((ep, i) => (
              <div key={i} style={{ background: PALETTE.tealSoft, border: `1px solid ${PALETTE.tealBorder}`, borderRadius: 8, padding: "10px 14px", fontFamily: FONTS.mono, fontSize: 18, color: PALETTE.tealLight }}>
                {ep}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
export const SceneDistributed: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const intro = spring({ frame, fps, config: { damping: 16, stiffness: 105 } });
  const expPop = interpolate(frame, [185, 215], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const p1 = (frame % 45) / 45;
  const p2 = ((frame + 22) % 45) / 45;
  return (
    <div style={{ position: "absolute", inset: 0, padding: "185px 52px 0 52px", display: "flex", flexDirection: "column" }}>
      <Img src={staticFile("bg/fiber_monolith.jpg")} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", opacity: 0.2 }} />
      <div style={{ position: "relative", zIndex: 2, opacity: intro, transform: `translateY(${(1 - intro) * 24}px)` }}>
        <div style={{ fontFamily: FONTS.mono, fontSize: 20, color: PALETTE.tealLight, fontWeight: 700, letterSpacing: 1 }}>
          05 // DISTRIBUTED COMPUTE
        </div>
        <div style={{ fontFamily: FONTS.display, fontSize: 58, fontWeight: 700, color: PALETTE.text, lineHeight: 1.08, marginTop: 10 }}>
          Pool Spare VRAM Across Machines.
        </div>
        <div style={{ marginTop: 26, background: "rgba(16,19,24,0.92)", border: `1px solid ${PALETTE.borderStrong}`, borderRadius: 16, padding: 24 }}>
          <svg width="928" height="270" viewBox="0 0 928 270">
            <rect x="20" y="24" width="250" height="92" rx="12" fill={PALETTE.surface} stroke={PALETTE.tealBorder} strokeWidth="2" />
            <text x="40" y="58" fill={PALETTE.tealLight} fontFamily={FONTS.mono} fontSize="16" fontWeight="700">WORKER NODE 01</text>
            <text x="40" y="88" fill={PALETTE.text} fontFamily={FONTS.sans} fontSize="20" fontWeight="600">RTX Laptop · +8 GB</text>
            <rect x="20" y="152" width="250" height="92" rx="12" fill={PALETTE.surface} stroke={PALETTE.tealBorder} strokeWidth="2" />
            <text x="40" y="186" fill={PALETTE.tealLight} fontFamily={FONTS.mono} fontSize="16" fontWeight="700">WORKER NODE 02</text>
            <text x="40" y="216" fill={PALETTE.text} fontFamily={FONTS.sans} fontSize="20" fontWeight="600">Workstation · +12 GB</text>
            <line x1="270" y1="70" x2="540" y2="135" stroke={PALETTE.teal} strokeWidth="3" strokeDasharray="8,6" />
            <line x1="270" y1="198" x2="540" y2="135" stroke={PALETTE.teal} strokeWidth="3" strokeDasharray="8,6" />
            <circle cx={270 + (540 - 270) * p1} cy={70 + (135 - 70) * p1} r="7" fill={PALETTE.tealLight} />
            <circle cx={270 + (540 - 270) * p2} cy={198 + (135 - 198) * p2} r="7" fill={PALETTE.tealLight} />
            <rect x="540" y="54" width="368" height="162" rx="14" fill={PALETTE.tealSoft} stroke={PALETTE.tealLight} strokeWidth="2.5" />
            <text x="566" y="96" fill={PALETTE.tealLight} fontFamily={FONTS.mono} fontSize="18" fontWeight="700">MASTER COORDINATOR (:8000)</text>
            <text x="566" y="134" fill={PALETTE.text} fontFamily={FONTS.display} fontSize="28" fontWeight="700">UDP :8001 + ggml-rpc</text>
            <text x="566" y="168" fill={PALETTE.textSec} fontFamily={FONTS.mono} fontSize="17">Consent VRAM &amp; CPU Thread Quotas</text>
            <text x="566" y="194" fill={PALETTE.success} fontFamily={FONTS.mono} fontSize="16">1s Heartbeats · Zero-Leak Tokens</text>
          </svg>
        </div>
        <div style={{ marginTop: 22, borderRadius: 14, overflow: "hidden", border: `1px solid ${PALETTE.borderStrong}`, boxShadow: "0 20px 50px rgba(0,0,0,0.65)" }}>
          <Img src={staticFile("shots/real-dashboard-lan.png")} style={{ width: "100%", display: "block", maxHeight: 410, objectFit: "cover", objectPosition: "top" }} />
        </div>
        <div style={{ marginTop: 22, opacity: expPop, transform: `translateY(${(1 - expPop) * 16}px)`, background: PALETTE.warningBg, border: `2px solid ${PALETTE.warningBorder}`, borderRadius: 12, padding: "16px 22px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <div style={{ fontFamily: FONTS.mono, fontSize: 17, fontWeight: 700, color: PALETTE.warning }}>
              EXPERIMENTAL SUBSYSTEM // P2P &amp; BLIND RELAY
            </div>
            <div style={{ fontFamily: FONTS.sans, fontSize: 20, color: PALETTE.textSec, marginTop: 4 }}>
              Ed25519 Node ID · X25519 + ChaCha20-Poly1305 AEAD · Self-hostable Relays
            </div>
          </div>
          <div style={{ padding: "8px 14px", borderRadius: 8, background: "rgba(240,179,71,0.16)", border: `1px solid ${PALETTE.warning}`, fontFamily: FONTS.mono, fontSize: 16, fontWeight: 700, color: PALETTE.warning }}>
            EXP
          </div>
        </div>
      </div>
    </div>
  );
};
export const SceneSoup: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 16, stiffness: 105 } });
  const activeStep = Math.min(4, Math.floor(interpolate(frame, [15, 220], [0, 5], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })));
  const steps = [
    ["01", "Consent Feedback", "Explicit user opt-in"],
    ["02", "Secret & PII Guard", "Masks API keys & PII"],
    ["03", "Immutable Snapshots", "SFT ChatML & DPO pairs"],
    ["04", "SOUP Training", "Dry-run, logs & cancel"],
    ["05", "Eval & Rollback", "Compare & 1-click restore"],
  ];
  return (
    <div style={{ position: "absolute", inset: 0, padding: "185px 52px 0 52px", display: "flex", flexDirection: "column" }}>
      <div style={{ opacity: enter, transform: `translateY(${(1 - enter) * 24}px)` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontFamily: FONTS.mono, fontSize: 20, color: PALETTE.info, fontWeight: 700, letterSpacing: 1 }}>06 // SHARD x SOUP ECOSYSTEM</span>
          <span style={{ padding: "5px 12px", borderRadius: 6, background: "#16272E", border: "1px solid #274D5C", fontFamily: FONTS.mono, fontSize: 15, fontWeight: 700, color: PALETTE.info }}>SINGLE-NODE HOST</span>
        </div>
        <div style={{ fontFamily: FONTS.display, fontSize: 58, fontWeight: 700, color: PALETTE.text, lineHeight: 1.08, marginTop: 10 }}>
          Customize Models on Your Own Data.
        </div>
        <div style={{ marginTop: 18, borderRadius: 14, overflow: "hidden", border: `1px solid ${PALETTE.borderStrong}`, boxShadow: "0 22px 55px rgba(0,0,0,0.65)" }}>
          <Img src={staticFile("shots/real-dashboard-training.png")} style={{ width: "100%", display: "block", maxHeight: 350, objectFit: "cover", objectPosition: "top" }} />
        </div>
        <div style={{ marginTop: 18, display: "flex", flexDirection: "column", gap: 9 }}>
          {steps.map(([num, title, sub], i) => {
            const isAct = i <= activeStep;
            return (
              <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "11px 18px", borderRadius: 10, background: isAct ? PALETTE.tealSoft : PALETTE.surface, border: `1px solid ${isAct ? PALETTE.tealBorder : PALETTE.border}` }}>
                <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                  <span style={{ fontFamily: FONTS.mono, fontSize: 17, fontWeight: 700, color: isAct ? PALETTE.tealLight : PALETTE.muted }}>{num}</span>
                  <span style={{ fontFamily: FONTS.sans, fontSize: 22, fontWeight: 700, color: PALETTE.text }}>{title}</span>
                </div>
                <span style={{ fontFamily: FONTS.mono, fontSize: 16.5, color: isAct ? PALETTE.tealLight : PALETTE.muted }}>{sub}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
export const SceneCTA: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pop = spring({ frame, fps, config: { damping: 14, stiffness: 100 } });
  const l1 = interpolate(frame, [8, 24], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const l2 = interpolate(frame, [24, 40], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const l3 = interpolate(frame, [40, 56], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const ctaBox = interpolate(frame, [70, 95], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", padding: "0 64px", textAlign: "center" }}>
      <Img src={staticFile("bg/gpu_blade.jpg")} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", opacity: 0.34 }} />
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(circle at 50% 45%, rgba(15,118,110,0.28) 0%, rgba(16,19,24,0.96) 72%)" }} />
      <div style={{ position: "relative", zIndex: 2, display: "flex", flexDirection: "column", alignItems: "center", width: "100%" }}>
        <div style={{ transform: `scale(${0.8 + pop * 0.2})`, opacity: pop }}>
          <ShardHexLogo size={150} strokeProgress={1} />
        </div>
        <div style={{ marginTop: 32, display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontFamily: FONTS.display, fontSize: 72, fontWeight: 700, color: PALETTE.text, letterSpacing: -1.5, opacity: l1 }}>Your models.</div>
          <div style={{ fontFamily: FONTS.display, fontSize: 72, fontWeight: 700, color: PALETTE.text, letterSpacing: -1.5, opacity: l2 }}>Your hardware.</div>
          <div style={{ fontFamily: FONTS.display, fontSize: 72, fontWeight: 700, color: PALETTE.tealLight, letterSpacing: -1.5, opacity: l3 }}>Your infrastructure.</div>
        </div>
        <div style={{ marginTop: 46, width: "100%", maxWidth: 880, background: "rgba(29,33,41,0.95)", border: `2px solid ${PALETTE.tealBorder}`, borderRadius: 16, padding: "28px 34px", opacity: ctaBox, transform: `translateY(${(1 - ctaBox) * 22}px)`, boxShadow: "0 24px 64px rgba(0,0,0,0.7)" }}>
          <div style={{ fontFamily: FONTS.sans, fontSize: 30, fontWeight: 700, color: PALETTE.text, marginBottom: 16 }}>
            Explore SHARD on GitHub
          </div>
          <div style={{ fontFamily: FONTS.mono, fontSize: 25, color: PALETTE.tealLight, fontWeight: 700 }}>
            github.com/avelone123123/shard
          </div>
          <div style={{ fontFamily: FONTS.mono, fontSize: 22, color: PALETTE.textSec, marginTop: 10 }}>
            avelone123123.github.io/shard/
          </div>
          <div style={{ marginTop: 20, display: "inline-block", padding: "12px 24px", borderRadius: 10, background: PALETTE.terminal, border: `1px solid ${PALETTE.borderStrong}`, fontFamily: FONTS.mono, fontSize: 24, color: PALETTE.success, fontWeight: 700 }}>
            $ pip install shard-engine
          </div>
        </div>
      </div>
    </div>
  );
};
