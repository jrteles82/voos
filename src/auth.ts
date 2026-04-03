import bcrypt from "bcryptjs";
import crypto from "node:crypto";

function timingSafeEqualHex(a: string, b: string): boolean {
  const aBuf = Buffer.from(a, "hex");
  const bBuf = Buffer.from(b, "hex");
  if (aBuf.length !== bBuf.length) {
    return false;
  }
  return crypto.timingSafeEqual(aBuf, bBuf);
}

function verifyWerkzeugScrypt(password: string, storedHash: string): boolean {
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
    const derived = crypto.scryptSync(password, salt, 64, { N: n, r, p, maxmem }).toString("hex");
    return timingSafeEqualHex(derived, expectedHex);
  } catch {
    return false;
  }
}

export async function verifyPassword(password: string, storedHash: string): Promise<boolean> {
  if (!storedHash) {
    return false;
  }
  if (storedHash.startsWith("$2")) {
    return bcrypt.compare(password, storedHash);
  }
  if (storedHash.startsWith("scrypt:")) {
    return verifyWerkzeugScrypt(password, storedHash);
  }
  return false;
}

export async function hashPassword(password: string): Promise<string> {
  return bcrypt.hash(password, 10);
}
