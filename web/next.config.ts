import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    // Without this, Turbopack's workspace-root auto-detection can walk up
    // past this project and pick an unrelated pnpm-workspace.yaml elsewhere
    // on the machine (e.g. in the user's home directory), which corrupts
    // output-file-tracing and produces an empty/broken Vercel build output.
    root: path.join(__dirname),
  },
};

export default nextConfig;
