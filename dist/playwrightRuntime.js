"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.ensurePlaywrightBrowsersPath = ensurePlaywrightBrowsersPath;
exports.ensurePlaywrightInstalled = ensurePlaywrightInstalled;
const node_fs_1 = __importDefault(require("node:fs"));
const node_os_1 = __importDefault(require("node:os"));
const node_path_1 = __importDefault(require("node:path"));
const node_child_process_1 = require("node:child_process");
const node_util_1 = require("node:util");
const execFileAsync = (0, node_util_1.promisify)(node_child_process_1.execFile);
let configured = false;
let installPromise = null;
function firstExistingDirectory(paths) {
    for (const candidate of paths) {
        if (node_fs_1.default.existsSync(candidate)) {
            return candidate;
        }
    }
    return undefined;
}
function ensureWritableDirectory(candidate) {
    try {
        node_fs_1.default.mkdirSync(candidate, { recursive: true });
        node_fs_1.default.accessSync(candidate, node_fs_1.default.constants.W_OK);
        return true;
    }
    catch {
        return false;
    }
}
function ensurePlaywrightBrowsersPath() {
    if (configured && process.env.PLAYWRIGHT_BROWSERS_PATH) {
        return;
    }
    if (process.env.PLAYWRIGHT_BROWSERS_PATH) {
        if (!ensureWritableDirectory(process.env.PLAYWRIGHT_BROWSERS_PATH)) {
            delete process.env.PLAYWRIGHT_BROWSERS_PATH;
        }
        else {
            configured = true;
            return;
        }
    }
    const browserPath = firstExistingDirectory([
        node_path_1.default.resolve(process.env.HOME ?? "", ".cache", "ms-playwright"),
        node_path_1.default.resolve(node_os_1.default.tmpdir(), "ms-playwright"),
        node_path_1.default.resolve(process.cwd(), ".playwright-browsers"),
        node_path_1.default.resolve(__dirname, "..", ".playwright-browsers")
    ]) ?? [
        node_path_1.default.resolve(process.env.HOME ?? "", ".cache", "ms-playwright"),
        node_path_1.default.resolve(node_os_1.default.tmpdir(), "ms-playwright"),
        node_path_1.default.resolve(process.cwd(), ".playwright-browsers"),
        node_path_1.default.resolve(__dirname, "..", ".playwright-browsers")
    ].find(ensureWritableDirectory);
    if (browserPath) {
        process.env.PLAYWRIGHT_BROWSERS_PATH = browserPath;
        configured = true;
        return;
    }
    configured = true;
}
async function ensurePlaywrightInstalled() {
    ensurePlaywrightBrowsersPath();
    const browsersPath = process.env.PLAYWRIGHT_BROWSERS_PATH;
    if (!browsersPath || !ensureWritableDirectory(browsersPath)) {
        throw new Error("Nao foi possivel preparar um diretorio gravavel para os browsers do Playwright.");
    }
    if (node_fs_1.default.readdirSync(browsersPath, { withFileTypes: true }).some((entry) => entry.isDirectory())) {
        return;
    }
    if (!installPromise) {
        const playwrightPackageJson = require.resolve("playwright/package.json");
        const playwrightCli = node_path_1.default.join(node_path_1.default.dirname(playwrightPackageJson), "cli.js");
        installPromise = execFileAsync(process.execPath, [playwrightCli, "install", "chromium"], {
            cwd: node_path_1.default.resolve(__dirname, ".."),
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
