import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: 'http://backend:8000/api/v1/:path*',
      },
      {
        source: '/delta-stream/:path*',
        destination: 'http://delta:8090/api/stream/:path*',
      },
      {
        source: '/delta-api/:path*',
        destination: 'http://delta:8090/api/:path*',
      },
    ];
  },
};

export default nextConfig;
