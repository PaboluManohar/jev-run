import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

// Load .env files from the repository root
const rootDir = fileURLToPath(new URL('../../', import.meta.url));
for (const name of ['.env.local', '.env']) {
  const envPath = resolve(rootDir, name);
  if (existsSync(envPath)) process.loadEnvFile(envPath);
}
const child = spawn(
  process.execPath,
  [
    fileURLToPath(new URL('./node_modules/next/dist/bin/next', import.meta.url)),
    process.argv[2] || 'dev',
    '--hostname',
    '127.0.0.1',
    '--port',
    process.env.STUDIO_PORT || '3040',
  ],
  { stdio: 'inherit', env: process.env, cwd: fileURLToPath(new URL('.', import.meta.url)) },
);
child.on('exit', (code) => {
  process.exitCode = code || 0;
});
process.on('SIGTERM', () => child.kill('SIGTERM'));
process.on('SIGINT', () => child.kill('SIGINT'));
