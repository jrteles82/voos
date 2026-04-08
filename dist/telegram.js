"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.sendTelegramMessage = sendTelegramMessage;
exports.sendTelegramPhoto = sendTelegramPhoto;
const config_1 = require("./config");
const node_fs_1 = __importDefault(require("node:fs"));
async function sendTelegramMessage(text, token, chatId) {
    const resolvedToken = token ?? process.env.TELEGRAM_BOT_TOKEN ?? config_1.config.telegram_bot_token;
    const resolvedChatId = chatId ?? process.env.TELEGRAM_CHAT_ID ?? config_1.config.telegram_chat_id;
    if (!resolvedToken || !resolvedChatId || resolvedToken === "TELEGRAM_BOT_TOKEN" || resolvedChatId === "TELEGRAM_CHAT_ID") {
        throw new Error("Telegram não configurado corretamente.");
    }
    const response = await fetch(`https://api.telegram.org/bot${resolvedToken}/sendMessage`, {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ chat_id: resolvedChatId, text })
    });
    if (!response.ok) {
        const details = await response.text().catch(() => "");
        throw new Error(`Falha Telegram: ${response.status}${details ? ` ${details}` : ""}`);
    }
}
async function sendTelegramPhoto(photoPath, caption = "", token, chatId) {
    const resolvedToken = token ?? process.env.TELEGRAM_BOT_TOKEN ?? config_1.config.telegram_bot_token;
    const resolvedChatId = chatId ?? process.env.TELEGRAM_CHAT_ID ?? config_1.config.telegram_chat_id;
    if (!resolvedToken || !resolvedChatId || resolvedToken === "TELEGRAM_BOT_TOKEN" || resolvedChatId === "TELEGRAM_CHAT_ID") {
        throw new Error("Telegram não configurado corretamente.");
    }
    if (!photoPath || !node_fs_1.default.existsSync(photoPath)) {
        throw new Error("Imagem da consulta não encontrada.");
    }
    const form = new FormData();
    form.set("chat_id", resolvedChatId);
    if (caption) {
        form.set("caption", caption);
    }
    form.set("photo", new Blob([node_fs_1.default.readFileSync(photoPath)]), "consulta.png");
    const response = await fetch(`https://api.telegram.org/bot${resolvedToken}/sendPhoto`, {
        method: "POST",
        body: form
    });
    if (!response.ok) {
        const details = await response.text().catch(() => "");
        throw new Error(`Falha Telegram photo: ${response.status}${details ? ` ${details}` : ""}`);
    }
}
