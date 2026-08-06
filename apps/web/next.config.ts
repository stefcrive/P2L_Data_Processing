import type { NextConfig } from "next";

const apiProxyTarget = (
  process.env.IRMS_API_PROXY_TARGET ||
  process.env.IRMS_API_URL ||
  "http://127.0.0.1:8000"
).replace(/\/+$/, "");
const distDir = process.env.NEXT_DIST_DIR || ".next";

const nextConfig: NextConfig = {
  distDir,
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/irms/:path*",
        destination: `${apiProxyTarget}/:path*`,
      },
    ];
  },
};

export default nextConfig;
