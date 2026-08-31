/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Enables the minimal standalone server.js output used by the production
  // Docker image (apps/admin-dashboard/Dockerfile) — avoids shipping the
  // full node_modules tree into the runtime container.
  output: "standalone",
};

export default nextConfig;
