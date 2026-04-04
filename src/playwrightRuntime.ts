import fs from "node:fs";
import path from "node:path";

let configured = false;

function firstExistingDirectory(paths: string[]): string | undefined {
  for (const candidate of paths) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  return undefined;
}

export function ensurePlaywrightBrowsersPath(): void {
  if (configured || process.env.PLAYWRIGHT_BROWSERS_PATH) {
    configured = true;
    return;
  }

  const browserPath = firstExistingDirectory([
    path.resolve(process.cwd(), ".playwright-browsers"),
    path.resolve(__dirname, "..", ".playwright-browsers"),
    path.resolve(process.env.HOME ?? "", ".cache", "ms-playwright")
  ]);

  if (browserPath) {
    process.env.PLAYWRIGHT_BROWSERS_PATH = browserPath;
  }

  configured = true;
}
