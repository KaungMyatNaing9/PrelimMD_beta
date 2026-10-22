import type { NextConfig } from "next";
// Hide the development-mode badge: the app is shown to people in dev mode.
const nextConfig: NextConfig = { devIndicators: false };
export default nextConfig;
