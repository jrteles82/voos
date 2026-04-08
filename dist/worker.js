"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const db_1 = require("./db");
const scanWorker_1 = require("./scanWorker");
async function main() {
    await (0, db_1.initDb)();
    const mode = (process.env.SKYSCANNER_WORKER_MODE ?? "daemon").trim().toLowerCase();
    if (mode === "once") {
        await (0, scanWorker_1.processDueUserScans)();
        await (0, scanWorker_1.runGlobalScan)();
        return;
    }
    (0, scanWorker_1.startWorkerSchedulers)();
}
main().catch((error) => {
    // eslint-disable-next-line no-console
    console.error("Failed to start worker", error);
    process.exit(1);
});
