/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',       // 独立输出，启动更快、内存更小
  compress: true,             // 启用 gzip 压缩
  poweredByHeader: false,
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  // Prevent "Failed to find Server Action" by disabling stale client cache
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          {
            key: 'Cache-Control',
            value: 'no-store, must-revalidate',
          },
        ],
      },
    ]
  },
  async redirects() {
    return [
      {
        source: '/',
        destination: '/dashboard',
        permanent: false,
      },
    ]
  },
}

export default nextConfig
