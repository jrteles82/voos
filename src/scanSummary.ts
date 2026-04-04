import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright";
import { ensurePlaywrightInstalled } from "./playwrightRuntime";
import { ScanRow } from "./types";

const PRICE_BAND_COLORS: Record<string, string> = {
  excelente: "🟢",
  bom: "🟡",
  normal: "🔵",
  caro: "🟤",
  sem_preco: "⚪️",
  novo: "🔵"
};

type SectionGroup = {
  title: string;
  badgeClass: string;
  highlightField: "origin" | "destination";
  rows: ScanRow[];
};

function priceNumber(row: ScanRow): number {
  return typeof row.price === "number" && Number.isFinite(row.price) ? row.price : Number.POSITIVE_INFINITY;
}

function dedupeSortedRows(rows: ScanRow[]): ScanRow[] {
  const seen = new Set<string>();
  const result: ScanRow[] = [];
  for (const row of rows) {
    const key = [
      row.origin.toUpperCase(),
      row.destination.toUpperCase(),
      row.outbound_date,
      row.inbound_date ?? ""
    ].join("|");
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(row);
  }
  return result;
}

function normalizeTrigger(trigger: string): string {
  if (trigger.startsWith("agendada")) {
    return "agendada";
  }
  if (trigger.includes("manual")) {
    return "manual";
  }
  return trigger;
}

function bestVendorLabel(row: ScanRow): string {
  return (row.best_vendor || row.final_price_source || row.site || "").trim();
}

function splitSections(rows: ScanRow[]): SectionGroup[] {
  const outbound = dedupeSortedRows(
    rows
      .filter((row) => row.origin.toUpperCase() === "PVH" && row.destination.toUpperCase() !== "PVH" && row.price !== null)
      .sort((a, b) => priceNumber(a) - priceNumber(b))
  );
  const inbound = dedupeSortedRows(
    rows
      .filter((row) => row.destination.toUpperCase() === "PVH" && row.price !== null)
      .sort((a, b) => priceNumber(a) - priceNumber(b))
  );

  return [
    {
      title: "IDAS (PVH -> destino):",
      badgeClass: "outbound",
      highlightField: "destination",
      rows: outbound
    },
    {
      title: "VOLTAS (destino -> PVH):",
      badgeClass: "inbound",
      highlightField: "origin",
      rows: inbound
    }
  ];
}

export function buildFullScanMessage(rows: ScanRow[], trigger = "manual"): string {
  if (!rows.length) {
    return "- ────────── ✈️ CONSULTA COMPLETA ✈️ ────────── -\nSem dados nesta execução.";
  }

  const sections = splitSections(rows);
  const lines: string[] = [
    "- ────────── ✈️ CONSULTA COMPLETA ✈️ ────────── -",
    `Execução: ${normalizeTrigger(trigger)}`,
    ""
  ];

  for (const [sectionIndex, section] of sections.entries()) {
    lines.push(section.title);
    if (!section.rows.length) {
      lines.push("N/D");
    } else {
      const bestKey = section.rows[0] ? `${section.rows[0].origin}|${section.rows[0].destination}|${section.rows[0].outbound_date}` : "";
      const grouped = new Map<string, ScanRow[]>();
      for (const row of section.rows) {
        const date = row.outbound_date || "";
        const list = grouped.get(date) ?? [];
        list.push(row);
        grouped.set(date, list);
      }

      for (const [dateIndex, date] of [...grouped.keys()].sort().entries()) {
        lines.push(date ? `📅 ${date}` : "📅 data pendente");
        for (const row of grouped.get(date) ?? []) {
          const vendor = bestVendorLabel(row);
          const vendorText = vendor ? ` | vendedor: ${vendor}` : "";
          const rowKey = `${row.origin}|${row.destination}|${row.outbound_date}`;
          const bestNote = rowKey === bestKey ? " | melhor preço" : "";
          lines.push(
            `${row.origin}→${row.destination} | ${PRICE_BAND_COLORS[row.price_band?.toLowerCase()] ?? "🔵"} ${row.outbound_date} | ${row.price_fmt}${vendorText}${bestNote}`
          );
        }
        if (dateIndex !== grouped.size - 1) {
          lines.push("");
        }
      }
    }
    if (sectionIndex !== sections.length - 1) {
      lines.push("");
    }
  }

  const totalOk = rows.filter((row) => row.price !== null).length;
  lines.push("", `Resumo: ${totalOk}/${rows.length} rotas com preço válido.`);
  return lines.join("\n");
}

function renderRows(rows: ScanRow[], title: string, highlightField: "origin" | "destination", badgeClass: string): string {
  if (!rows.length) {
    return `
      <section class="section">
        <div class="section-title ${badgeClass}">${title.replace(":", "")}</div>
        <div class="empty">Nenhum preço válido nesta seção.</div>
      </section>
    `;
  }

  const bestKey = `${rows[0].origin}|${rows[0].destination}|${rows[0].outbound_date}`;
  const body = rows.map((row) => {
    const rowKey = `${row.origin}|${row.destination}|${row.outbound_date}`;
    const vendor = bestVendorLabel(row);
    const vendorPrice = row.best_vendor_price != null ? ` (${new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(row.best_vendor_price)})` : "";
    const route = highlightField === "destination"
      ? `${row.origin} → <span class="city">${row.destination}</span>`
      : `<span class="city">${row.origin}</span> → ${row.destination}`;
    const bestChip = rowKey === bestKey ? '<span class="best-chip">melhor preço</span>' : "";
    return `
      <tr>
        <td>${route}</td>
        <td><span class="date-pill ${badgeClass}">${row.outbound_date}</span></td>
        <td class="price">${row.price_fmt}</td>
        <td>${vendor || row.site}${vendorPrice} ${bestChip}</td>
      </tr>
    `;
  }).join("");

  return `
    <section class="section">
      <div class="section-title ${badgeClass}">${title.replace(":", "")}</div>
      <table>
        <tbody>${body}</tbody>
      </table>
    </section>
  `;
}

export async function buildScanResultsImage(rows: ScanRow[], title = "Consulta completa"): Promise<string | null> {
  const validRows = rows.filter((row) => row.price !== null);
  if (!validRows.length) {
    return null;
  }

  const sections = splitSections(validRows);
  const timestamp = new Intl.DateTimeFormat("pt-BR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date());

  const html = `
    <!doctype html>
    <html lang="pt-BR">
      <head>
        <meta charset="utf-8" />
        <style>
          body {
            margin: 0;
            background: #eef2f6;
            font-family: Arial, sans-serif;
            color: #243041;
          }
          .wrap {
            width: 760px;
            padding: 18px;
          }
          h1 {
            margin: 0 0 4px;
            font-size: 26px;
          }
          .subtitle {
            font-size: 14px;
            color: #64748b;
            margin-bottom: 14px;
          }
          .section {
            margin-bottom: 12px;
            border: 1px solid #cbd5e1;
            background: #fff;
          }
          .section-title {
            font-size: 18px;
            font-weight: 700;
            text-align: center;
            padding: 8px 12px;
            background: #d9dee7;
          }
          .section-title.inbound {
            background: #f6e7b5;
          }
          table {
            width: 100%;
            border-collapse: collapse;
          }
          td {
            border-top: 1px solid #d8dee8;
            padding: 10px 12px;
            font-size: 16px;
            vertical-align: middle;
          }
          .city {
            color: #5aa1ee;
          }
          .price {
            color: #16825d;
            font-weight: 700;
            font-size: 18px;
            white-space: nowrap;
          }
          .date-pill {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 999px;
            background: #dbeafe;
          }
          .date-pill.inbound {
            background: #fef3c7;
          }
          .best-chip {
            display: inline-block;
            margin-left: 6px;
            padding: 2px 8px;
            border-radius: 999px;
            background: #dcfce7;
            color: #166534;
            font-size: 12px;
            font-weight: 700;
          }
          .empty {
            padding: 14px;
            font-size: 15px;
            color: #64748b;
          }
        </style>
      </head>
      <body>
        <div class="wrap">
          <h1>${title}</h1>
          <div class="subtitle">${timestamp}</div>
          ${renderRows(sections[0].rows, "IDAS (menor → maior preço):", "destination", "outbound")}
          ${renderRows(sections[1].rows, "VOLTAS PARA PVH (menor → maior preço):", "origin", "inbound")}
        </div>
      </body>
    </html>
  `;

  await ensurePlaywrightInstalled();
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 796, height: 1200 }, deviceScaleFactor: 1 });
    await page.setContent(html, { waitUntil: "load" });
    const element = await page.locator(".wrap").elementHandle();
    if (!element) {
      return null;
    }
    const imagePath = path.join(os.tmpdir(), `telegram-scan-${Date.now()}.png`);
    await element.screenshot({ path: imagePath });
    return imagePath;
  } finally {
    await browser.close();
  }
}
