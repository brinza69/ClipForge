import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  webpack: (config, { dev }) => {
    if (dev) {
      config.watchOptions = { poll: 800, aggregateTimeout: 200 };
    }
    return config;
  },
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
      { protocol: "http", hostname: "localhost" },
      { protocol: "http", hostname: "127.0.0.1" },
    ],
  },
  async rewrites() {
    return [
      {
        source: "/worker-api/:path*",
        destination: "http://localhost:8420/api/:path*",
      },
      {
        source: "/worker-thumbnails/:path*",
        destination: "http://localhost:8420/thumbnails/:path*",
      },
    ];
  },
};

export default nextConfig;
