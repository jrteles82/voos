"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.launchChromium = launchChromium;
const node_fs_1 = __importDefault(require("node:fs"));
const node_path_1 = __importDefault(require("node:path"));
const playwright_1 = require("playwright");
const config_1 = require("./config");
function ensureBrowserPath() {
    const browserPath = config_1.appConfig.playwright.browsersPath;
    if (!browserPath || config_1.appConfig.playwright.skipBrowserDownload) {
        return;
    }
    process.env.PLAYWRIGHT_BROWSERS_PATH = browserPath;
    node_fs_1.default.mkdirSync(node_path_1.default.resolve(browserPath), { recursive: true });
}
async function launchChromium(options = {}) {
    ensureBrowserPath();
    const executablePath = config_1.appConfig.playwright.executablePath;
    const args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        ...(options.args ?? [])
    ];
    return playwright_1.chromium.launch({
        headless: true,
        ...options,
        args,
        executablePath: executablePath || options.executablePath
    });
}
