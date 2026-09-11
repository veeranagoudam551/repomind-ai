import path from "path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    root: path.join(__dirname),
  },
  // Day 47: traces the minimal file set (including select node_modules)
  // needed to run in production into .next/standalone, so the frontend
  // Dockerfile's final image doesn't need `npm ci`'d node_modules or the
  // rest of the source tree at all - just that folder plus the static/
  // public assets copied in alongside it.
  output: "standalone",
};

export default nextConfig;
