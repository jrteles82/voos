"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.utcNowIso = utcNowIso;
exports.formatBrl = formatBrl;
exports.parsePriceBrl = parsePriceBrl;
exports.classifyPrice = classifyPrice;
exports.buildConfigQueries = buildConfigQueries;
exports.extractFinalPriceSource = extractFinalPriceSource;
exports.toRoute = toRoute;
exports.filterRowsByMaxPrice = filterRowsByMaxPrice;
const config_1 = require("./config");
function utcNowIso() {
    return new Date().toISOString();
}
function formatBrl(value) {
    if (value === null || value === undefined || Number.isNaN(value)) {
        return "sem preço";
    }
    return new Intl.NumberFormat("pt-BR", {
        style: "currency",
        currency: "BRL"
    }).format(value);
}
function parsePriceBrl(text) {
    const cleaned = text.replace(/\u00a0/g, " ");
    const matches = cleaned.match(/R\$\s*([\d.]+(?:,\d{2})?)/);
    if (!matches) {
        return null;
    }
    const value = Number(matches[1].replace(/\./g, "").replace(",", "."));
    return Number.isFinite(value) ? value : null;
}
function classifyPrice(price, minPrice, avgPrice) {
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
function buildConfigQueries() {
    const destinations = [...config_1.config.destinations_br];
    if (config_1.config.enable_south_america) {
        destinations.push(...config_1.config.destinations_sa);
    }
    const queries = [];
    const seen = new Set();
    for (const destination of destinations) {
        for (const outboundDate of config_1.config.outbound_dates) {
            const tripType = "oneway";
            const key = [config_1.config.origin, destination, outboundDate, "", tripType].join("|");
            if (!seen.has(key)) {
                seen.add(key);
                queries.push({
                    origin: config_1.config.origin,
                    destination,
                    outboundDate,
                    inboundDate: "",
                    tripType
                });
            }
        }
        for (const inboundDate of config_1.config.inbound_dates) {
            const tripType = "oneway";
            const key = [destination, config_1.config.origin, inboundDate, "", tripType].join("|");
            if (!seen.has(key)) {
                seen.add(key);
                queries.push({
                    origin: destination,
                    destination: config_1.config.origin,
                    outboundDate: inboundDate,
                    inboundDate: "",
                    tripType
                });
            }
        }
    }
    return queries;
}
function extractFinalPriceSource(notes) {
    const text = notes ?? "";
    const match = text.match(/final_price_source=([^|]+)/);
    return match?.[1]?.trim() ?? "";
}
function toRoute(input) {
    const origin = String(input.origin ?? config_1.config.origin).trim().toUpperCase();
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
function filterRowsByMaxPrice(rows, maxPrice) {
    if (maxPrice === null) {
        return rows;
    }
    return rows.filter((row) => row.price === null || row.price <= maxPrice);
}
