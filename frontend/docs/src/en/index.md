---
layout: home
editLink: true
lastUpdated: true

hero:
  name: "FastapiAdmin"
  text: "Web · H5 · Mini Program — one stack"
  tagline: Built on FastAPI + Vue3 + TypeScript, frontend and backend share types. 200+ REST endpoints out of the box, the AI Code Generator turns a table into full-stack CRUD, and Docker Compose brings the whole stack online in one command.
  image:
    src: /logo.svg
    alt: FastapiAdmin brand mark
  actions:
    - theme: brand
      text: Quick Start
      link: /en/guide/start
    - theme: alt
      text: Live Demo
      link: https://service.fastapiadmin.com/web
      target: _blank
    - theme: alt
      text: Mobile
      link: https://service.fastapiadmin.com/app
      target: _blank

features:
  - icon: 📦
    title: Full-Stack & Ready
    details: FastAPI async backend + Vue3 web + UniApp mobile, ready to run. One brand token set covers Web, H5 and mini programs.

  - icon: ⚡
    title: High-Performance Async
    details: Native async/await, Pydantic validation, Redis caching and SQLAlchemy 2.0 keep the whole request path async.

  - icon: 🤖
    title: AI Code Generator
    details: Pick a table and generate Controller / Service / Model / Vue pages plus types. Routine CRUD stops consuming engineering time.

  - icon: 🛡️
    title: Enterprise RBAC
    details: JWT + OAuth2 with menu, button and data-level permissions, plus full operation audit logs.

  - icon: 🐳
    title: One-Click Docker Deploy
    details: Docker Compose orchestrates backend, web, Nginx and SSL. One command to production, with multi-environment support.

  - icon: 📱
    title: Web + Mobile Unified
    details: UniApp + Wot Design Uni. One codebase for H5, WeChat / Alipay mini programs and native App, with shared auth, RBAC and dark mode.
---

<script setup lang="ts">
import HomeSections from "../../.vitepress/components/HomeSections.vue";
</script>

<HomeSections />
