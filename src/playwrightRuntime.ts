import fs from "node:fs";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

let configured = false;
let installPromise: Promise<void> | null = null;

function firstExistingDirectory(paths: string[]): string | undefined {
  for (const candidate of paths) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  return undefined;
}

export function ensurePlaywrightBrowsersPath(): void {
  if (configured && process.env.PLAYWRIGHT_BROWSERS_PATH) {
    return;
  }

  if (process.env.PLAYWRIGHT_BROWSERS_PATH) {
    configured = true;
    return;
  }

  process.env.PLAYWRIGHT_BROWSERS_PATH = path.resolve(__dirname, "..", ".playwright-browsers");

  configured = true;
}

export async function ensurePlaywrightInstalled(): Promise<void> {
  ensurePlaywrightBrowsersPath();

  const browsersPath = process.env.PLAYWRIGHT_BROWSERS_PATH!;
  fs.mkdirSync(browsersPath, { recursive: true });

  if (fs.readdirSync(browsersPath, { withFileTypes: true }).some((entry) => entry.isDirectory())) {
    return;
  }

  if (!installPromise) {
    installPromise = execFileAsync("npx", ["playwright", "install", "chromium"], {
      cwd: path.resolve(__dirname, ".."),
      env: {
        ...process.env,
        PLAYWRIGHT_BROWSERS_PATH: browsersPath
      }
    }).then(() => undefined).finally(() => {
      installPromise = null;
    });
  }

  await installPromise;
}
