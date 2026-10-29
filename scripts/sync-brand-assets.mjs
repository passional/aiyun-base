#!/usr/bin/env node
/**
 * 品牌素材单一来源：design/assets
 * 将 logo / favicon 同步到官网、Web、小程序与后端验证码。
 */
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const source = resolve(root, "design/assets");

const targets = [
  ["logo.svg", "frontend/web/public/logo.svg"],
  ["logo.png", "frontend/web/public/logo.png"],
  ["favicon.ico", "frontend/web/public/favicon.ico"],
  ["logo.svg", "frontend/web/src/assets/fa_imgs/logo.svg"],
  ["logo.svg", "frontend/docs/src/public/logo.svg"],
  ["favicon.ico", "frontend/docs/src/public/favicon.ico"],
  ["logo.png", "frontend/app/src/static/logo.png"],
  ["favicon.ico", "frontend/app/src/static/favicon.ico"],
  ["logo.svg", "backend/app/modules/system/auth/assets/logo.svg"],
];

for (const [from, to] of targets) {
  const dest = resolve(root, to);
  mkdirSync(dirname(dest), { recursive: true });
  copyFileSync(resolve(source, from), dest);
  console.log(`synced ${to}`);
}
