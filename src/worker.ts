import { initDb } from "./db";
import { processDueUserScans, runGlobalScan, startWorkerSchedulers } from "./scanWorker";

async function main() {
  await initDb();

  const mode = (process.env.SKYSCANNER_WORKER_MODE ?? "daemon").trim().toLowerCase();
  if (mode === "once") {
    await processDueUserScans();
    await runGlobalScan();
    return;
  }

  startWorkerSchedulers();
}

main().catch((error) => {
  // eslint-disable-next-line no-console
  console.error("Failed to start worker", error);
  process.exit(1);
});
