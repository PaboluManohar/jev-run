# NetrisTV ws-scrcpy — Local Production Runtime

This directory contains a production build of the open-source [NetrisTV/ws-scrcpy](https://github.com/NetrisTV/ws-scrcpy) project, prepared to run locally without keeping the original source tree or development dependencies.

## Build process

The production build was created from the upstream repository using:

```bash
npm install
npm run dist:prod
```

The generated `dist/` output was copied into this runtime directory. Production dependencies were then installed:

```bash
npm install --omit=dev
```

The original source repository and build-time files were removed only after the runtime copy was prepared.

## Run locally

Requirements:
- Node.js and npm
- Android Debug Bridge (ADB), if connecting an Android device

Start the server directly from the repository root:

```bash
make stream
# Or to use a custom port (e.g. 8005):
# STREAMING_PORT=8005 make stream
```

Or from the `ws-scrcpy-runtime` directory:

```bash
npm start
```

By default, the server listens on **HTTP port 8000**. Open:

<http://localhost:8000>

To connect an Android device, enable **USB debugging**, connect it by USB, approve the debugging prompt, and verify that ADB detects it:

```bash
adb devices
```

Stop the server with `Ctrl+C`.

## Runtime contents

- `index.js` — compiled Node.js server
- `public/` — compiled web UI assets
- `vendor/` — bundled vendor assets, including the scrcpy server JAR
- `node_modules/` — production Node.js dependencies
- `package.json` and `package-lock.json` — runtime package metadata
- `LICENSE` — upstream license file

## Notes

- This is a compiled runtime, not the full upstream development repository.
- The server currently uses HTTP on port **8000**; port 8443 is not configured.
- The upstream production build completed successfully, with webpack bundle-size warnings.
- `npm install` reported dependency vulnerabilities. Review them with `npm audit` before using this build in a security-sensitive environment.
- Keep the upstream license and comply with the project's license and notices when redistributing this build.