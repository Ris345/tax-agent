import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Secure S3 Upload',
  description: 'Upload PDF and image files to a private S3 bucket with SSE-KMS encryption.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-white text-gray-900 antialiased">{children}</body>
    </html>
  );
}
