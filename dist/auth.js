"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.verifyPassword = verifyPassword;
exports.hashPassword = hashPassword;
const bcryptjs_1 = __importDefault(require("bcryptjs"));
const node_crypto_1 = __importDefault(require("node:crypto"));
function timingSafeEqualHex(a, b) {
    const aBuf = Buffer.from(a, "hex");
    const bBuf = Buffer.from(b, "hex");
    if (aBuf.length !== bBuf.length) {
        return false;
    }
    return node_crypto_1.default.timingSafeEqual(aBuf, bBuf);
}
function verifyWerkzeugScrypt(password, storedHash) {
    const [method, salt, expectedHex] = storedHash.split("$");
    if (!method?.startsWith("scrypt:") || !salt || !expectedHex) {
        return false;
    }
    const [, nRaw, rRaw, pRaw] = method.split(":");
    const n = Number(nRaw);
    const r = Number(rRaw);
    const p = Number(pRaw);
    if (!Number.isFinite(n) || !Number.isFinite(r) || !Number.isFinite(p)) {
        return false;
    }
    try {
        // Some existing hashes can exceed Node's default scrypt memory cap.
        // Keep enough headroom for verification while staying bounded.
        const maxmem = 128 * n * r * 2;
        const derived = node_crypto_1.default.scryptSync(password, salt, 64, { N: n, r, p, maxmem }).toString("hex");
        return timingSafeEqualHex(derived, expectedHex);
    }
    catch {
        return false;
    }
}
async function verifyPassword(password, storedHash) {
    if (!storedHash) {
        return false;
    }
    if (storedHash.startsWith("$2")) {
        return bcryptjs_1.default.compare(password, storedHash);
    }
    if (storedHash.startsWith("scrypt:")) {
        return verifyWerkzeugScrypt(password, storedHash);
    }
    return false;
}
async function hashPassword(password) {
    return bcryptjs_1.default.hash(password, 10);
}
