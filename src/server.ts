import app, { startSchedulers } from "./app";
import { appConfig } from "./config";
import { initDb } from "./db";

async function main() {
  await initDb();
  if (appConfig.internalSchedulerEnabled) {
    startSchedulers();
  }
  app.listen(appConfig.port, appConfig.host, () => {
    // eslint-disable-next-line no-console
    console.log(`Server listening on http://${appConfig.host}:${appConfig.port}`);
  });
}

main().catch((error) => {
  // eslint-disable-next-line no-console
  console.error("Failed to start server", error);
  process.exit(1);
});
