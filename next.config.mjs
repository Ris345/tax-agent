/** @type {import('next').NextConfig} */
const nextConfig = {
  // Prevent bundling AWS SDK server-side modules into client bundles
  serverExternalPackages: ['@aws-sdk/client-s3', '@aws-sdk/s3-presigned-post', '@aws-sdk/s3-request-presigner'],
};

export default nextConfig;
