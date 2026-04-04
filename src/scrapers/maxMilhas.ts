import { chromium } from "playwright";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { config } from "../config";
import { ensurePlaywrightInstalled } from "../playwrightRuntime";
import { FlightResult, RouteQuery } from "../types";

function buildMaxMilhasUrl(route: RouteQuery): string {
  return `https://www.maxmilhas.com.br/busca-passagens-aereas/OW/${route.origin}/${route.destination}/${route.outboundDate}/1/0/0/EC`;
}

function filterInstallmentPrices(prices: number[]): number[] {
  const candidates = [...new Set(prices.map((price) => Math.round(price * 100) / 100))].sort((a, b) => a - b);
  const totals = new Set(candidates);
  const filtered = candidates.filter((price) => {
    for (let installments = 2; installments <= 12; installments += 1) {
      if (totals.has(Math.round(price * installments * 100) / 100)) {
        return false;
      }
    }
    return true;
  });
  return filtered.length ? filtered : candidates;
}

function parseVisiblePrices(text: string): number[] {
  return Array.from(text.matchAll(/R\$\s*([\d.]+,\d{2})/g))
    .map((match) => Number(match[1].replace(/\./g, "").replace(",", ".")))
    .filter((value) => Number.isFinite(value) && value > 200);
}

export async function searchMaxMilhas(route: RouteQuery): Promise<FlightResult | null> {
  if (route.inboundDate) {
    return null;
  }

  await ensurePlaywrightInstalled();
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
  });

  try {
    const context = await browser.newContext({
      locale: "pt-BR",
      timezoneId: "America/Porto_Velho",
      viewport: { width: 1366, height: 768 }
    });
    const page = await context.newPage();
    let screenshotPath: string | undefined;
    await page.goto(buildMaxMilhasUrl(route), { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(5000);

    try {
      await page.getByRole("button", { name: /aceitar todos/i }).click({ timeout: 1500 });
      await page.waitForTimeout(500);
    } catch {
      // Cookie banner is not always present.
    }

    try {
      await page.waitForSelector("text=/R\\$\\s*[\\d.]+,\\d{2}/", { timeout: 15000 });
    } catch {
      // Fall back to best-effort text scraping below.
    }

    const bodyText = await page.locator("body").innerText({ timeout: 10000 }).catch(() => "");
    const candidateTexts = await page.locator("body, main, [data-testid*='flight'], [class*='price'], [class*='fare']").allInnerTexts().catch(() => []);
    const prices = [
      ...parseVisiblePrices(bodyText),
      ...candidateTexts.flatMap((text) => parseVisiblePrices(text))
    ];
    const filtered = filterInstallmentPrices(prices)
      .filter((price) => price >= Number(config.maxmilhas_min_price));

    let selectedPrice: number | null = null;
    for (const price of filtered) {
      if (price >= Number(config.maxmilhas_final_price_threshold)) {
        selectedPrice = price;
        break;
      }
    }
    if (selectedPrice === null && filtered.length) {
      selectedPrice = filtered[filtered.length - 1];
    }

    screenshotPath = path.join(os.tmpdir(), `consulta-maxmilhas-${route.origin}-${route.destination}-${Date.now()}.png`);
    await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => undefined);
    if (screenshotPath && !fs.existsSync(screenshotPath)) {
      screenshotPath = undefined;
    }

    return {
      site: "maxmilhas",
      origin: route.origin,
      destination: route.destination,
      outboundDate: route.outboundDate,
      inboundDate: route.inboundDate,
      tripType: route.tripType,
      price: selectedPrice,
      currency: "BRL",
      url: page.url(),
      notes: `final_price_source=maxmilhas | precos=${JSON.stringify(filtered)} | capturados=${JSON.stringify(prices.slice(0, 20))}`,
      bestVendor: selectedPrice !== null ? "MaxMilhas" : "",
      bestVendorPrice: selectedPrice,
      bookingOptionsJson: JSON.stringify(selectedPrice !== null ? [{ vendor: "MaxMilhas", price: selectedPrice }] : []),
      screenshotPath
    };
  } catch (error) {
    return {
      site: "maxmilhas",
      origin: route.origin,
      destination: route.destination,
      outboundDate: route.outboundDate,
      inboundDate: route.inboundDate,
      tripType: route.tripType,
      price: null,
      currency: "BRL",
      url: buildMaxMilhasUrl(route),
      notes: `erro=${error instanceof Error ? error.message : "desconhecido"}`,
      bestVendor: "",
      bestVendorPrice: null,
      bookingOptionsJson: "[]"
    };
  } finally {
    await browser.close().catch(() => undefined);
  }
}
