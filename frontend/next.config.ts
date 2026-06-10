import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin Turbopack's workspace root to this app (frontend/). Without this,
  // Next infers the git repo root and dev mode watches the ENTIRE repo: any
  // file written elsewhere during a live council run (wandb/weave output,
  // __pycache__, tooling logs) fires a Fast Refresh rebuild every few hundred
  // milliseconds, pinning the browser main thread and freezing the Decision
  // Room. That rebuild storm — not CSS/motion animations — was the real cause
  // of the historical "live run lags/crashes the page" problem.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
