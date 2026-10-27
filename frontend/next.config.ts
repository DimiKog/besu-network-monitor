import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

const proxyTarget = (
  process.env.BESU_MONITOR_DEV_API_PROXY_TARGET ||
  "https://api.monitor.dimikog.org"
).replace(/\/+$/, "");

const nextConfig: NextConfig = {
  images: {
    unoptimized: true,
  },
  ...(isDev
    ? {
        // Dev-only proxy avoids needing production CORS for localhost.
        // Static export builds omit rewrites and use output: "export".
        async rewrites() {
          return [
            {
              source: "/monitor-api/:path*",
              destination: `${proxyTarget}/:path*`,
            },
          ];
        },
      }
    : {
        output: "export",
        // Helps GitHub Pages resolve nested routes consistently.
        trailingSlash: true,
      }),
};

export default nextConfig;
