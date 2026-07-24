import type { Download } from '@playwright/test';

export async function readDownload(download: Download): Promise<string> {
  const stream = await download.createReadStream();
  let content = '';
  for await (const chunk of stream) content += chunk.toString();
  return content;
}

export const secretArtifactPattern =
  /cookie\s*=|bearer\s+|access_token|refresh_token|otp\s*[:=]|proxy_password|profile_path|biometric|13800138000|824911|dlp-canary/i;
