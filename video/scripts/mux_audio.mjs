import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
const root = path.resolve(import.meta.dirname, "..");
const ffmpeg = path.join(root, "node_modules", "@remotion", "compositor-win32-x64-msvc", "ffmpeg.exe");
const ffBin = fs.existsSync(ffmpeg) ? ffmpeg : "ffmpeg";
const rawVideo = path.resolve(root, "..", "artifacts", "SHARD_promo.video.mp4");
const audio = path.join(root, "public", "audio", "master_audio.mp3");
const outMp4 = path.resolve(root, "..", "artifacts", "SHARD_promo.mp4");
execFileSync(ffBin, [
  "-y",
  "-i", rawVideo,
  "-i", audio,
  "-map", "0:v:0",
  "-map", "1:a:0",
  "-c:v", "copy",
  "-c:a", "aac",
  "-b:a", "192k",
  "-ar", "48000",
  "-ac", "2",
  "-movflags", "+faststart",
  "-shortest",
  outMp4,
], { stdio: "inherit" });
fs.unlinkSync(rawVideo);
