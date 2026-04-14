import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {},
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
    ];
  },
};

export default nextConfig;
