import fs from "node:fs";
import os from "node:os";
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

function ensureWritableDirectory(candidate: string): boolean {
  try {
    fs.mkdirSync(candidate, { recursive: true });
    fs.accessSync(candidate, fs.constants.W_OK);
    return true;
  } catch {
    return false;
  }
}

export function ensurePlaywrightBrowsersPath(): void {
  if (configured && process.env.PLAYWRIGHT_BROWSERS_PATH) {
    return;
  }

  if (process.env.PLAYWRIGHT_BROWSERS_PATH) {
    if (!ensureWritableDirectory(process.env.PLAYWRIGHT_BROWSERS_PATH)) {
      delete process.env.PLAYWRIGHT_BROWSERS_PATH;
    } else {
      configured = true;
      return;
    }
  }

  const browserPath = firstExistingDirectory([
    path.resolve(process.env.HOME ?? "", ".cache", "ms-playwright"),
    path.resolve(os.tmpdir(), "ms-playwright"),
    path.resolve(process.cwd(), ".playwright-browsers"),
    path.resolve(__dirname, "..", ".playwright-browsers")
  ]) ?? [
    path.resolve(process.env.HOME ?? "", ".cache", "ms-playwright"),
    path.resolve(os.tmpdir(), "ms-playwright"),
    path.resolve(process.cwd(), ".playwright-browsers"),
    path.resolve(__dirname, "..", ".playwright-browsers")
  ].find(ensureWritableDirectory);

  if (browserPath) {
    process.env.PLAYWRIGHT_BROWSERS_PATH = browserPath;
    configured = true;
    return;
  }

  configured = true;
}

export async function ensurePlaywrightInstalled(): Promise<void> {
  ensurePlaywrightBrowsersPath();

  const browsersPath = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (!browsersPath || !ensureWritableDirectory(browsersPath)) {
    throw new Error("Nao foi possivel preparar um diretorio gravavel para os browsers do Playwright.");
  }

  if (fs.readdirSync(browsersPath, { withFileTypes: true }).some((entry) => entry.isDirectory())) {
    return;
  }

  if (!installPromise) {
    const playwrightPackageJson = require.resolve("playwright/package.json");
    const playwrightCli = path.join(path.dirname(playwrightPackageJson), "cli.js");
    installPromise = execFileAsync(process.execPath, [playwrightCli, "install", "chromium"], {
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
