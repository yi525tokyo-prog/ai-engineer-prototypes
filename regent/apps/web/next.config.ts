import type { NextConfig } from "next";

const api = process.env.REGENT_API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  output: "standalone",
  async rewrites() {
    // The cockpit talks to the Regent API through same-origin /api/* paths.
    return [
      { source: "/api/:path*", destination: `${api}/api/:path*` },
      { source: "/sim/:path*", destination: `${api}/sim/:path*` },
    ];
  },
};

export default config;
