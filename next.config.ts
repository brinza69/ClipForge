import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {},
  // Some backend endpoints (TikTok metadata via yt-dlp, transcript clean via
  // local Ollama, full pipeline polling) can hold an HTTP connection for
  // 1-3 minutes. Default proxy timeout (~30s) was killing those with a
  // "socket hang up" before the response could arrive.
  experimental: {
    proxyTimeout: 5 * 60 * 1000,  // 5 minutes
  },
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
      { protocol: "http", hostname: "localhost" },
      { protocol: "http", hostname: "127.0.0.1" },
    ],
  },
  async rewrites() {
    const workerBase = process.env.WORKER_URL_INTERNAL || "http://127.0.0.1:8420";
    return [
      {
        source: "/worker-api/:path*",
        destination: `${workerBase}/api/:path*`,
      },
      {
        source: "/worker-thumbnails/:path*",
        destination: `${workerBase}/thumbnails/:path*`,
      },
      {
        source: "/worker-doodle/:path*",
        destination: `${workerBase}/doodle-files/:path*`,
      },
    ];
  },
};

export default nextConfig;
