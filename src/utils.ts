import { config } from "./config";
import { RouteQuery, TripType } from "./types";

export function utcNowIso(): string {
  return new Date().toISOString();
}

export function formatBrl(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "sem preço";
  }
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL"
  }).format(value);
}

export function parsePriceBrl(text: string): number | null {
  const cleaned = text.replace(/\u00a0/g, " ");
  const matches = cleaned.match(/R\$\s*([\d.]+(?:,\d{2})?)/);
  if (!matches) {
    return null;
  }
  const value = Number(matches[1].replace(/\./g, "").replace(",", "."));
  return Number.isFinite(value) ? value : null;
}

export function classifyPrice(price: number | null, minPrice: number | null, avgPrice: number | null): string {
  if (price === null) {
    return "sem_preco";
  }
  if (minPrice === null && avgPrice === null) {
    return "novo";
  }
  if (minPrice !== null && price <= minPrice) {
    return "excelente";
  }
  if (avgPrice !== null && price <= avgPrice * 0.92) {
    return "bom";
  }
  if (avgPrice !== null && price >= avgPrice * 1.15) {
    return "caro";
  }
  return "normal";
}

export function buildConfigQueries(): RouteQuery[] {
  const destinations = [...config.destinations_br];
  if (config.enable_south_america) {
    destinations.push(...config.destinations_sa);
  }

  const queries: RouteQuery[] = [];
  const seen = new Set<string>();
  for (const destination of destinations) {
    for (const outboundDate of config.outbound_dates) {
      const tripType: TripType = "oneway";
      const key = [config.origin, destination, outboundDate, "", tripType].join("|");
      if (!seen.has(key)) {
        seen.add(key);
        queries.push({
          origin: config.origin,
          destination,
          outboundDate,
          inboundDate: "",
          tripType
        });
      }
    }
    for (const inboundDate of config.inbound_dates) {
      const tripType: TripType = "oneway";
      const key = [destination, config.origin, inboundDate, "", tripType].join("|");
      if (!seen.has(key)) {
        seen.add(key);
        queries.push({
          origin: destination,
          destination: config.origin,
          outboundDate: inboundDate,
          inboundDate: "",
          tripType
        });
      }
    }
  }
  return queries;
}

export function extractFinalPriceSource(notes: string | null | undefined): string {
  const text = notes ?? "";
  const match = text.match(/final_price_source=([^|]+)/);
  return match?.[1]?.trim() ?? "";
}

export function toRoute(input: Record<string, unknown>): RouteQuery {
  const origin = String(input.origin ?? config.origin).trim().toUpperCase();
  const destination = String(input.destination ?? "JPA").trim().toUpperCase();
  const outboundDate = String(input.outbound_date ?? "").trim();
  const inboundDate = String(input.inbound_date ?? "").trim();
  if (!outboundDate) {
    throw new Error("Parâmetro obrigatório: outbound_date (YYYY-MM-DD)");
  }
  return {
    origin,
    destination,
    outboundDate,
    inboundDate,
    tripType: inboundDate ? "roundtrip" : "oneway"
  };
}

export function filterRowsByMaxPrice<T extends { price: number | null }>(rows: T[], maxPrice: number | null): T[] {
  if (maxPrice === null) {
    return rows;
  }
  return rows.filter((row) => row.price === null || row.price <= maxPrice);
}
