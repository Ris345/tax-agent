import FileUpload from '@/components/FileUpload';

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8 gap-8">
      <div className="text-center space-y-2">
        <h1 className="text-2xl font-bold tracking-tight">Secure Document Upload</h1>
        <p className="text-sm text-gray-500">
          Files are uploaded directly to S3 using a short-lived presigned URL.
          <br />
          All objects are encrypted at rest with SSE-KMS and stored in a private bucket.
        </p>
      </div>

      <FileUpload />

      <p className="text-xs text-gray-400">
        A Lambda function is notified automatically on every successful upload via S3 event notification.
      </p>
    </main>
  );
}
