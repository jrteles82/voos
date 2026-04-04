import { BrowserContext, chromium } from "playwright";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { config } from "../config";
import { ensurePlaywrightBrowsersPath } from "../playwrightRuntime";
import { FlightResult, RouteQuery } from "../types";
import { formatBrl } from "../utils";

function buildGoogleFlightsUrl(route: RouteQuery): string {
  const q = route.tripType === "oneway"
    ? `${route.origin} to ${route.destination} ${route.outboundDate} one way`
    : `${route.origin} to ${route.destination} ${route.outboundDate} return ${route.inboundDate}`;
  return `https://www.google.com/travel/flights?q=${encodeURIComponent(q)}&hl=pt-BR&gl=BR&curr=BRL`;
}

function parsePrice(raw: string): number | null {
  const match = raw.match(/R\$\s*([\d.]+(?:,\d{2})?)/);
  if (!match) {
    return null;
  }
  const value = Number(match[1].replace(/\./g, "").replace(",", "."));
  return Number.isFinite(value) ? value : null;
}

export async function searchGoogleFlights(route: RouteQuery): Promise<FlightResult> {
  ensurePlaywrightBrowsersPath();
  const browser = await chromium.launch({ headless: Boolean(config.headless) });
  let context: BrowserContext | null = null;
  let screenshotPath: string | undefined;
  try {
    context = await browser.newContext({
      locale: "pt-BR",
      timezoneId: "America/Porto_Velho",
      userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    });
    const page = await context.newPage();
    page.setDefaultTimeout(Number(config.timeout_ms));
    const notes: string[] = [];
    await page.goto(buildGoogleFlightsUrl(route), { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);

    for (const label of ["Aceitar tudo", "Aceito", "I agree", "Accept all"]) {
      try {
        await page.getByRole("button", { name: label }).click({ timeout: 1200 });
        await page.waitForTimeout(800);
        break;
      } catch {
        continue;
      }
    }

    try {
      await page.waitForLoadState("networkidle", { timeout: 6000 });
    } catch {
      notes.push("networkidle_timeout");
    }
    await page.waitForTimeout(Math.min(1000, Number(config.settle_seconds) * 1000));

    const bodyText = await page.locator("body").innerText({ timeout: 5000 }).catch(() => "");
    const allPrices = Array.from(bodyText.matchAll(/R\$\s*([\d.]+(?:,\d{2})?)/g))
      .map((match) => Number(match[1].replace(/\./g, "").replace(",", ".")))
      .filter((value) => Number.isFinite(value) && value > 100);
    const visibleMinPrice = allPrices.length ? Math.min(...allPrices) : null;

    if (visibleMinPrice !== null) {
      notes.push(`visible_min_price=${formatBrl(visibleMinPrice)}`);
      notes.push("final_price_source=visible_list");
    } else {
      notes.push("Preço não identificado automaticamente.");
    }

    screenshotPath = path.join(os.tmpdir(), `consulta-google-${route.origin}-${route.destination}-${Date.now()}.png`);
    await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => undefined);
    if (screenshotPath && !fs.existsSync(screenshotPath)) {
      screenshotPath = undefined;
    }

    return {
      site: "google_flights",
      origin: route.origin,
      destination: route.destination,
      outboundDate: route.outboundDate,
      inboundDate: route.inboundDate,
      tripType: route.tripType,
      price: visibleMinPrice,
      currency: "BRL",
      url: page.url(),
      notes: notes.join(" | "),
      bestVendor: "",
      bestVendorPrice: null,
      bookingOptionsJson: "[]",
      screenshotPath
    };
  } catch (error) {
    return {
      site: "google_flights",
      origin: route.origin,
      destination: route.destination,
      outboundDate: route.outboundDate,
      inboundDate: route.inboundDate,
      tripType: route.tripType,
      price: null,
      currency: "BRL",
      url: buildGoogleFlightsUrl(route),
      notes: `erro=${error instanceof Error ? error.message : "desconhecido"}`,
      bestVendor: "",
      bestVendorPrice: null,
      bookingOptionsJson: "[]",
      screenshotPath
    };
  } finally {
    await context?.close().catch(() => undefined);
    await browser.close().catch(() => undefined);
  }
}
