"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const app_1 = __importDefault(require("./app"));
const config_1 = require("./config");
const db_1 = require("./db");
async function main() {
    await (0, db_1.initDb)();
    app_1.default.listen(config_1.appConfig.port, config_1.appConfig.host, () => {
        // eslint-disable-next-line no-console
        console.log(`Server listening on http://${config_1.appConfig.host}:${config_1.appConfig.port}`);
    });
}
main().catch((error) => {
    // eslint-disable-next-line no-console
    console.error("Failed to start server", error);
    process.exit(1);
});
