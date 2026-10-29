---
layout: home
editLink: true
lastUpdated: true

hero:
  name: "FastapiAdmin"
  text: "Web · H5 · 小程序，一套全栈"
  tagline: 基于 FastAPI + Vue3 + TypeScript，前后端类型共享。200+ REST 接口开箱即用，AI 代码生成器选表即出全栈 CRUD，Docker Compose 一条命令上线。
  image:
    src: /logo.svg
    alt: FastapiAdmin 品牌标识
  actions:
    - theme: brand
      text: 立即开始
      link: /guide/start
    - theme: alt
      text: 在线预览
      link: https://service.fastapiadmin.com/web
      target: _blank
    - theme: alt
      text: 移动端
      link: https://service.fastapiadmin.com/app
      target: _blank

features:
  - icon: 📦
    title: 全栈开箱即用
    details: FastAPI 异步后端 + Vue3 Web + UniApp 移动端，克隆即跑；同一套品牌 Token 贯穿 Web、H5 与小程序。

  - icon: ⚡
    title: 高性能异步架构
    details: 原生 async/await、Pydantic 自动校验、Redis 缓存与 SQLAlchemy 2.0，从接口到数据层保持异步。

  - icon: 🤖
    title: AI 代码生成器
    details: 选表 → 自动生成 Controller / Service / Model / Vue 页面与类型定义，常规 CRUD 不再手写。

  - icon: 🛡️
    title: 企业级 RBAC 权限
    details: JWT + OAuth2 认证，菜单 / 按钮 / 数据三级权限，操作日志全审计。

  - icon: 🐳
    title: Docker 一键部署
    details: Docker Compose 编排后端、Web、Nginx 与 SSL，一条命令上线，支持多环境配置。

  - icon: 📱
    title: Web + 移动端一体
    details: 基于 UniApp 与 Wot Design Uni，一套代码生成 H5、微信 / 支付宝小程序与 App，登录、权限与暗色模式同源。
---

<script setup lang="ts">
import HomeSections from "../.vitepress/components/HomeSections.vue";
</script>

<HomeSections />
