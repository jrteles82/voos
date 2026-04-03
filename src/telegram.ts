import { config } from "./config";
import fs from "node:fs";

export async function sendTelegramMessage(text: string, token?: string, chatId?: string): Promise<void> {
  const resolvedToken = token ?? process.env.TELEGRAM_BOT_TOKEN ?? config.telegram_bot_token;
  const resolvedChatId = chatId ?? process.env.TELEGRAM_CHAT_ID ?? config.telegram_chat_id;
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

export async function sendTelegramPhoto(photoPath: string, caption = "", token?: string, chatId?: string): Promise<void> {
  const resolvedToken = token ?? process.env.TELEGRAM_BOT_TOKEN ?? config.telegram_bot_token;
  const resolvedChatId = chatId ?? process.env.TELEGRAM_CHAT_ID ?? config.telegram_chat_id;
  if (!resolvedToken || !resolvedChatId || resolvedToken === "TELEGRAM_BOT_TOKEN" || resolvedChatId === "TELEGRAM_CHAT_ID") {
    throw new Error("Telegram não configurado corretamente.");
  }
  if (!photoPath || !fs.existsSync(photoPath)) {
    throw new Error("Imagem da consulta não encontrada.");
  }

  const form = new FormData();
  form.set("chat_id", resolvedChatId);
  if (caption) {
    form.set("caption", caption);
  }
  form.set("photo", new Blob([fs.readFileSync(photoPath)]), "consulta.png");

  const response = await fetch(`https://api.telegram.org/bot${resolvedToken}/sendPhoto`, {
    method: "POST",
    body: form
  });

  if (!response.ok) {
    const details = await response.text().catch(() => "");
    throw new Error(`Falha Telegram photo: ${response.status}${details ? ` ${details}` : ""}`);
  }
}
