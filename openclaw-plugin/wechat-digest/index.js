import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PLUGIN_ID = "wechat-digest";
const DEFAULT_PREFIX = "wd";
const DEFAULT_CHANNEL = "openclaw-weixin";
const DEFAULT_MAX_REPLY_CHARS = 6000;
const DEFAULT_TIMEOUT_MS = 600000;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const stringParam = (description) => ({
  type: "object",
  additionalProperties: false,
  properties: {
    value: { type: "string", description },
    maxChars: { type: "integer", minimum: 500, description: "Optional output cap." },
  },
  required: ["value"],
});

const noParam = {
  type: "object",
  additionalProperties: false,
  properties: {
    maxChars: { type: "integer", minimum: 500, description: "Optional output cap." },
  },
};

export function extractPrefixedCommand(text, prefix = DEFAULT_PREFIX) {
  const trimmed = String(text ?? "").trim();
  if (!trimmed) return null;
  const normalizedPrefix = String(prefix || DEFAULT_PREFIX).trim().toLowerCase();
  if (!normalizedPrefix) return null;
  for (const candidate of commandCandidates(trimmed)) {
    const lower = candidate.toLowerCase();
    if (lower === normalizedPrefix) return "帮助";
    if (lower.startsWith(normalizedPrefix + " ")) {
      return candidate.slice(normalizedPrefix.length).trim() || "帮助";
    }
  }
  return null;
}

function commandCandidates(text) {
  const candidates = [];
  const push = (value) => {
    const trimmed = String(value ?? "").trim();
    if (trimmed) candidates.push(trimmed);
  };
  push(text);
  for (const line of text.split(/\r?\n/)) {
    push(line);
    push(line.replace(/^\[[^\]]+\]\s*/, ""));
    const bodyMatch = line.match(/\bBodyForAgent\s*:\s*(.+)$/i);
    if (bodyMatch) push(bodyMatch[1]);
  }
  return candidates;
}

export function buildToolArgs(action, params = {}, runtime = {}) {
  const maxChars = resolveMaxChars(params.maxChars, runtime.maxReplyChars);
  if (action === "status") {
    return ["status", "--max-chars", String(maxChars)];
  }
  if (action === "command") {
    const command = String(params.command ?? params.value ?? "").trim();
    if (!command) throw new Error("command is required");
    return ["handle", "--max-chars", String(maxChars), command];
  }
  if (action === "backfill") {
    const date = String(params.date ?? params.value ?? "yesterday").trim() || "yesterday";
    return ["digest-backfill", "--date", date, "--print", "--allow-fallback", "--max-chars", String(maxChars)];
  }
  if (action === "once") {
    const group = String(params.group ?? params.value ?? "").trim();
    if (!group) throw new Error("group is required");
    const date = String(params.date ?? "today").trim() || "today";
    const args = [
      "fetch-once",
      "--group",
      group,
      "--date",
      date,
      "--delivery",
      "print",
      "--max-chars",
      String(maxChars),
    ];
    const maxScrolls = Number(params.maxScrolls);
    if (Number.isInteger(maxScrolls) && maxScrolls >= 0) {
      args.push("--max-scrolls", String(maxScrolls));
    }
    return args;
  }
  if (action === "search") {
    const query = String(params.query ?? params.value ?? "").trim();
    if (!query) throw new Error("query is required");
    const args = ["search", "--max-chars", String(maxChars)];
    const group = String(params.group ?? "").trim();
    if (group) args.push("--group", group);
    args.push(query);
    return args;
  }
  if (action === "context") {
    const messageRef = String(params.messageRef ?? params.value ?? "").trim();
    if (!messageRef) throw new Error("messageRef is required");
    return ["context", "--max-chars", String(maxChars), messageRef];
  }
  if (action === "memory") {
    const date = String(params.date ?? params.value ?? "yesterday").trim() || "yesterday";
    return ["handle", "--max-chars", String(maxChars), `记忆 ${date}`];
  }
  throw new Error(`Unsupported action: ${action}`);
}

function resolveRuntime(api) {
  const pluginConfig = api?.pluginConfig && typeof api.pluginConfig === "object" ? api.pluginConfig : {};
  const rootFromConfig = asString(pluginConfig.projectRoot);
  const projectRoot = path.resolve(rootFromConfig || path.join(__dirname, "..", ".."));
  const cliPath = path.resolve(projectRoot, asString(pluginConfig.cliPath) || defaultCliPath(projectRoot));
  return {
    projectRoot,
    cliPath,
    configPath: path.resolve(projectRoot, asString(pluginConfig.configPath) || "config.yaml"),
    envPath: path.resolve(projectRoot, asString(pluginConfig.envPath) || ".env"),
    channel: asString(pluginConfig.channel, DEFAULT_CHANNEL),
    prefix: asString(pluginConfig.prefix, DEFAULT_PREFIX),
    allowGroupCommands: Boolean(pluginConfig.allowGroupCommands),
    maxReplyChars: resolveMaxChars(pluginConfig.maxReplyChars, DEFAULT_MAX_REPLY_CHARS),
    timeoutMs: resolvePositiveInt(pluginConfig.timeoutMs, DEFAULT_TIMEOUT_MS),
  };
}

function defaultCliPath(projectRoot) {
  if (process.platform === "win32") {
    return path.join(".venv", "Scripts", "wechat-digest.exe");
  }
  return path.join(".venv", "bin", "wechat-digest");
}

function asString(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function resolvePositiveInt(value, fallback) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

function resolveMaxChars(value, fallback) {
  return Math.max(500, resolvePositiveInt(value, fallback || DEFAULT_MAX_REPLY_CHARS));
}

function toolResult(text, details = {}) {
  return {
    content: [{ type: "text", text }],
    details,
  };
}

function applyCap(text, maxChars) {
  if (text.length <= maxChars) return text;
  return `${text.slice(0, maxChars).trimEnd()}\n\n[输出已截断，仅显示前 ${maxChars} 字。请缩小查询范围或用 wd 原文 m123 查看上下文。]`;
}

async function runWechatDigest(api, action, params, metadata = {}) {
  const runtime = resolveRuntime(api);
  if (!fs.existsSync(runtime.projectRoot)) {
    throw new Error(`projectRoot not found: ${runtime.projectRoot}`);
  }
  if (!fs.existsSync(runtime.cliPath)) {
    throw new Error(`wechat-digest CLI not found: ${runtime.cliPath}`);
  }
  const args = [
    "--config",
    runtime.configPath,
    "--env",
    runtime.envPath,
    ...buildToolArgs(action, params, runtime),
  ];
  const result = await spawnFixed(runtime.cliPath, args, runtime, metadata);
  const text = applyCap(result.stdout.trim() || result.stderr.trim() || "(no output)", runtime.maxReplyChars);
  if (result.exitCode !== 0) {
    throw new Error(text);
  }
  return toolResult(text, { action, exitCode: result.exitCode });
}

function spawnFixed(command, args, runtime, metadata = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: runtime.projectRoot,
      shell: false,
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
        ...Object.fromEntries(
          Object.entries(metadata).filter(([, value]) => typeof value === "string" && value.trim()),
        ),
      },
    });
    const stdout = [];
    const stderr = [];
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, runtime.timeoutMs);
    child.stdout.on("data", (chunk) => stdout.push(Buffer.from(chunk)));
    child.stderr.on("data", (chunk) => stderr.push(Buffer.from(chunk)));
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (exitCode) => {
      clearTimeout(timer);
      const out = Buffer.concat(stdout).toString("utf8");
      const err = Buffer.concat(stderr).toString("utf8");
      if (timedOut) {
        reject(new Error(`wechat-digest timed out after ${runtime.timeoutMs}ms`));
        return;
      }
      resolve({ exitCode: exitCode ?? 1, stdout: out, stderr: err });
    });
  });
}

function firstString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

function eventText(event, ctx) {
  return firstString(
    event?.cleanedBody,
    event?.BodyForAgent,
    event?.bodyForAgent,
    event?.content,
    event?.body,
    event?.text,
    ctx?.BodyForAgent,
    ctx?.bodyForAgent,
    ctx?.content,
    ctx?.body,
    ctx?.text,
  );
}

function eventChannel(event, ctx) {
  return firstString(
    event?.channel,
    event?.channelId,
    event?.messageProvider,
    ctx?.messageProvider,
    ctx?.channel,
    ctx?.channelId,
  );
}

function eventIsGroup(event, ctx) {
  return Boolean(event?.isGroup ?? event?.group ?? ctx?.isGroup ?? ctx?.group);
}

function eventSessionKey(event, ctx) {
  return firstString(
    event?.session_key,
    event?.sessionKey,
    event?.conversation_id,
    event?.conversationId,
    event?.conversation,
    event?.from_user_id,
    event?.fromUserId,
    event?.sender_id,
    event?.senderId,
    event?.user_id,
    event?.userId,
    ctx?.session_key,
    ctx?.sessionKey,
    ctx?.conversation_id,
    ctx?.conversationId,
    ctx?.conversation,
    ctx?.from_user_id,
    ctx?.fromUserId,
    ctx?.sender_id,
    ctx?.senderId,
    ctx?.user_id,
    ctx?.userId,
    event?.route?.session_key,
    event?.route?.sessionKey,
    event?.route?.conversation_id,
    event?.route?.conversationId,
    event?.route?.from_user_id,
    event?.route?.fromUserId,
    ctx?.route?.session_key,
    ctx?.route?.sessionKey,
    ctx?.route?.conversation_id,
    ctx?.route?.conversationId,
    ctx?.route?.from_user_id,
    ctx?.route?.fromUserId,
  );
}

function eventSourceMessageId(event, ctx) {
  return firstString(
    event?.message_id,
    event?.messageId,
    event?.id,
    event?.client_id,
    event?.clientId,
    ctx?.message_id,
    ctx?.messageId,
    ctx?.id,
  );
}

function shouldClaim(event, ctx, runtime) {
  const text = eventText(event, ctx);
  if (!text) return null;
  const channel = eventChannel(event, ctx);
  if (runtime.channel && channel && channel !== runtime.channel) return null;
  if (eventIsGroup(event, ctx) && !runtime.allowGroupCommands) return null;
  return extractPrefixedCommand(text, runtime.prefix);
}

async function handlePrefixedCommand(api, event, ctx) {
  const runtime = resolveRuntime(api);
  const command = shouldClaim(event, ctx, runtime);
  if (command === null) return;
  try {
    api.logger?.info?.(`wechat-digest: claimed ${runtime.prefix} command via ${ctx?.trigger ?? "inbound"}`);
    const result = await runWechatDigest(
      api,
      "command",
      { command, maxChars: runtime.maxReplyChars },
      {
        WECHAT_DIGEST_REPLY_SESSION_KEY: eventSessionKey(event, ctx),
        WECHAT_DIGEST_REPLY_SOURCE_MESSAGE_ID: eventSourceMessageId(event, ctx),
      },
    );
    return { handled: true, reply: { text: result.content[0].text } };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { handled: true, reply: { text: `执行失败：${applyCap(message, runtime.maxReplyChars)}`, isError: true } };
  }
}

export default {
  id: PLUGIN_ID,
  name: "WeChat Digest Tools",
  description: "Expose the local WeChat digest project as fixed OpenClaw tools.",
  configSchema: {
    type: "object",
    additionalProperties: false,
  },
  register(api) {
    api.registerTool({
      name: "wechat_digest_command",
      label: "WeChat Digest Command",
      description: "Run one wd-prefixed WeChat digest command such as wd 日报 昨天, wd 查 报名, wd 群, or wd 记忆.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          command: { type: "string", description: "Command text without the wd prefix." },
          maxChars: { type: "integer", minimum: 500, description: "Optional output cap." },
        },
        required: ["command"],
      },
      async execute(_id, params) {
        return runWechatDigest(api, "command", params);
      },
    });

    api.registerTool({
      name: "wechat_digest_status",
      label: "WeChat Digest Status",
      description: "Show local WeChat digest database and configuration status.",
      parameters: noParam,
      async execute(_id, params) {
        return runWechatDigest(api, "status", params);
      },
    });

    api.registerTool({
      name: "wechat_digest_backfill",
      label: "WeChat Digest Backfill",
      description: "Backfill one date from PC WeChat, generate a digest, and return the text without sending from this project.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          date: { type: "string", description: "Date, today, yesterday, or YYYY-MM-DD. Defaults to yesterday." },
          maxChars: { type: "integer", minimum: 500, description: "Optional output cap." },
        },
      },
      async execute(_id, params) {
        return runWechatDigest(api, "backfill", params);
      },
    });

    api.registerTool({
      name: "wechat_digest_once",
      label: "WeChat Digest Once",
      description: "Fetch one group/date from PC WeChat, summarize it with the current digest prompt, and return the text.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          group: { type: "string", description: "WeChat group name to fetch." },
          date: { type: "string", description: "today, yesterday, or YYYY-MM-DD. Defaults to today." },
          maxScrolls: { type: "integer", minimum: 0, description: "Optional older-message scroll rounds." },
          maxChars: { type: "integer", minimum: 500, description: "Optional output cap." },
        },
        required: ["group"],
      },
      async execute(_id, params) {
        return runWechatDigest(api, "once", params);
      },
    });

    api.registerTool({
      name: "wechat_digest_search",
      label: "WeChat Digest Search",
      description: "Search the local WeChat digest database.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          query: { type: "string", description: "Search keywords." },
          group: { type: "string", description: "Optional group name filter." },
          maxChars: { type: "integer", minimum: 500, description: "Optional output cap." },
        },
        required: ["query"],
      },
      async execute(_id, params) {
        return runWechatDigest(api, "search", params);
      },
    });

    api.registerTool({
      name: "wechat_digest_context",
      label: "WeChat Digest Context",
      description: "Show local context around a message reference such as m123.",
      parameters: stringParam("Message reference, e.g. m123 or 123."),
      async execute(_id, params) {
        return runWechatDigest(api, "context", { messageRef: params.value, maxChars: params.maxChars });
      },
    });

    const hookOptions = { priority: 1000, timeoutMs: DEFAULT_TIMEOUT_MS };
    api.on("inbound_claim", (event, ctx) => handlePrefixedCommand(api, event, ctx), hookOptions);
    api.on("before_agent_reply", (event, ctx) => handlePrefixedCommand(api, event, ctx), hookOptions);
  },
};
