#!/usr/bin/env node
/**
 * 品牌一致性检查：Logo 哈希一致、无旧色值、无旧间距类。
 * 用法：node scripts/check-brand-consistency.mjs
 */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";

const root = resolve(import.meta.dirname, "..");
const errors = [];

function hash(file) {
  return createHash("sha256").update(readFileSync(resolve(root, file))).digest("hex");
}

const logoGroups = [
  ["design/assets/logo.svg", "frontend/web/public/logo.svg", "frontend/docs/src/public/logo.svg", "frontend/web/src/assets/fa_imgs/logo.svg", "backend/app/modules/system/auth/assets/logo.svg"],
  ["design/assets/logo.png", "frontend/web/public/logo.png", "frontend/app/src/static/logo.png"],
  ["design/assets/favicon.ico", "frontend/web/public/favicon.ico", "frontend/docs/src/public/favicon.ico", "frontend/app/src/static/favicon.ico"],
];

for (const group of logoGroups) {
  const [source, ...targets] = group;
  const expected = hash(source);
  for (const target of targets) {
    if (hash(target) !== expected) errors.push(`Logo 不一致：${target}`);
  }
}

const legacyColors = ["#6366f1", "#8b5cf6", "#ec4899", "#a78bfa", "#5b6cf7", "#06b6d4", "#c4b5fd", "#f9a8d4"];
const searchRoots = ["frontend/docs/.vitepress", "frontend/app/src"];
let text = "";
try {
  text = execFileSync(
    "grep",
    ["-rnE", legacyColors.join("|"), ...searchRoots, "--include=*.vue", "--include=*.css", "--include=*.scss", "--include=*.ts"],
    { cwd: root, encoding: "utf8" },
  );
} catch (error) {
  if (error.status !== 1) throw error;
}
if (text.trim()) errors.push(`发现旧品牌色：\n${text}`);

let spacing = "";
try {
  spacing = execFileSync(
    "grep",
    ["-rnE", "mx-3|mb-3|rounded-2", "frontend/app/src", "--include=*.vue"],
    { cwd: root, encoding: "utf8" },
  );
} catch (error) {
  if (error.status !== 1) throw error;
}
if (spacing.trim()) errors.push(`发现旧间距/圆角类：\n${spacing}`);

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log("品牌一致性检查通过：Logo、颜色、间距、圆角");
