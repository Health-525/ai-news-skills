import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

function argumentValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function findModule(root, predicate, depth = 0) {
  if (depth > 8 || !fs.existsSync(root)) return undefined;
  let entries;
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch {
    return undefined;
  }
  for (const entry of entries) {
    const candidate = path.join(root, entry.name);
    if (entry.isFile() && predicate(candidate)) return candidate;
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const result = findModule(path.join(root, entry.name), predicate, depth + 1);
    if (result) return result;
  }
  return undefined;
}

function resolveSendModule() {
  const configured = process.env.OPENCLAW_FEISHU_SEND_MODULE;
  if (configured && fs.existsSync(configured)) return configured;
  const roots = [
    path.join(process.env.HOME || "", ".openclaw", "npm", "projects"),
    path.join(process.env.HOME || "", ".npm-global", "lib", "node_modules"),
  ];
  for (const root of roots) {
    const result = findModule(
      root,
      (candidate) => candidate.includes(`${path.sep}feishu${path.sep}dist${path.sep}send-`) && candidate.endsWith(".js"),
    );
    if (result) return result;
  }
  return undefined;
}

function resolveConfigModule() {
  const configured = process.env.OPENCLAW_CONFIG_MODULE;
  if (configured && fs.existsSync(configured)) return configured;
  const roots = [
    path.join(process.env.HOME || "", ".npm-global", "lib", "node_modules", "openclaw", "dist"),
    path.join(process.env.HOME || "", ".openclaw", "npm", "projects"),
  ];
  for (const root of roots) {
    const result = findModule(root, (candidate) => path.basename(candidate).startsWith("io-") && candidate.endsWith(".js"));
    if (result) return result;
  }
  return undefined;
}

const target = argumentValue("--target");
const cardJson = argumentValue("--card-json");
const accountId = argumentValue("--account") || "default";
if (!target || !cardJson) {
  console.error("send_feishu_card requires --target and --card-json");
  process.exit(2);
}

const sendModulePath = resolveSendModule();
const configModulePath = resolveConfigModule();
if (!sendModulePath || !configModulePath) {
  console.error("OpenClaw Feishu modules could not be located");
  process.exit(1);
}

try {
  const sendModule = await import(pathToFileURL(sendModulePath));
  const configModule = await import(pathToFileURL(configModulePath));
  const sendCardFeishu = sendModule.sendCardFeishu ?? sendModule.i;
  const loadConfig = configModule.loadConfig ?? configModule.a;
  if (typeof sendCardFeishu !== "function" || typeof loadConfig !== "function") {
    throw new Error("OpenClaw Feishu module exports are incompatible");
  }
  const response = await sendCardFeishu({
    cfg: loadConfig(),
    to: target,
    card: JSON.parse(cardJson),
    accountId,
  });
  const messageId = response?.messageId ?? response?.receipt?.primaryPlatformMessageId;
  if (!messageId) throw new Error("Feishu card send returned no message ID");
  console.log(JSON.stringify({ status: "sent", message_id: messageId }));
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
