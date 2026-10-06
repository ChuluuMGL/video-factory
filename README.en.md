# Video Factory

> Deploy video workflows to your own server with an Agent Skill.
>
> Feishu Base and n8n by default, with reusable infrastructure for multiple projects.

[中文](README.md) | **English**

[Installation guide](https://www.yueyu.tech/zh/products/video-factory/) · [Pinned release](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a31) · [Setup Skill](skills/video-factory-setup/SKILL.md) · [Security](SECURITY.md)

## What it does

Video Factory connects product information, script generation, video generation and human review into a maintainable project workflow. Administrators install and configure it through an AI agent and the CLI; teams organize tasks in their own Feishu Base by default.

- **Independent deployment:** customers manage their services, database, accounts and credentials.
- **Multiple projects:** reuse infrastructure while configuring products, Feishu and models per project.
- **Resumable operations:** one Skill supports installation, new projects, interrupted setup and diagnosis.

The installer runs on the customer's server. The Skill guides the AI agent. The public website explains the product and installation; it does not store customer project accounts or model keys.

## Version and availability

**0.1.0a31 · MIT-licensed public alpha.**

Source, the complete Skill and pinned server packages are publicly readable without GitHub login. Customers provide their own server, Feishu and model accounts.

a31 passed 247 core tests, anonymous fixed-package installation, browser flows, PostgreSQL/n8n upgrade and recovery, and offline checks with both Docker storage modes. Earlier a29 testing covered real Feishu Base creation, task import and script revision/review, with an agent operating a single account. **Real model generation on a new current-version deployment, separate employee identities and complete independent customer acceptance remain unverified.** Historical project examples do not establish acceptance of this installer.

See the [release verification record](docs/product/DELIVERY_A31_RESULT.md) and [current status](docs/product/STATUS.md).

## Get started

Use the [installation guide](https://www.yueyu.tech/zh/products/video-factory/) to download the complete Skill and give it to an agent that can read files and operate your server.

**You can also start directly from GitHub:**

1. Read the [installation and handoff guide](docs/product/CUSTOMER_GUIDE.md).
2. Have your agent read the complete [`skills/video-factory-setup/`](skills/video-factory-setup/) directory, including `references/`.
3. Give it the prompt below. The Skill guides acquisition and verification of the pinned release, followed by interactive setup.

```text
Use the complete Skill in skills/video-factory-setup/ to install
Video Factory on my own server and configure the first test project.
Use version 0.1.0a31. Check the server and release package first,
then guide me through Setup. Use private input channels for passwords
and API keys; do not collect them in chat.
```

Prepare an Ubuntu 24.04 x86_64 server with at least 4 GiB RAM, server access, project information, a Feishu application and model accounts. The default CLI installation does not require a business subdomain. The agent checks and prepares Python 3.12, Docker and Compose.

Pinned packages support anonymous download. Development branches and temporary Actions artifacts are not supported release packages.

The operational guides and installer interactions are currently primarily in Chinese. This English README does not imply that the full product is localized.

## After installation

| Ask the agent to | Expected behavior |
|---|---|
| Add a project | Reuse the service and create separate project configuration; no Skill or n8n reinstallation |
| Resume setup | Check the original session and deployment before continuing |
| Diagnose a project | Start with read-only checks of the server, Feishu, model and task state |
| Upgrade services | Check compatibility, back up and preserve rollback; updating the Skill does not upgrade the server |

## Deployed components

| Component | Location and purpose |
|---|---|
| Setup Skill | Administrator's or installer's agent; installation and maintenance |
| Product services, PostgreSQL, n8n | Customer server; configuration and task execution |
| Feishu Base | Project information, source tasks and employees' daily view |
| Controlled review commands | Agent-assisted import and review with the operator's Feishu identity |

The public a31 release does not automatically write generated results back to Base. Existing web review code is outside the recommended first-install path; full Feishu-side review, result writeback and independent employee acceptance remain open. See the [product shape and acceptance boundary](docs/product/PRODUCT_SHAPE_2026_10_06.md).

## Documentation and repository scope

- Installers: [customer guide](docs/product/CUSTOMER_GUIDE.md), [complete Skill](skills/video-factory-setup/SKILL.md), [acceptance guide](docs/product/INDEPENDENT_ACCEPTANCE.md).
- Maintainers: [product definition](docs/product/PRD.md), [status](docs/product/STATUS.md), [migration scope](docs/product/MIGRATION.md), [publication review](docs/product/PUBLICATION_REVIEW.md).
- Security and data: [security reporting](SECURITY.md), [data handling](PRIVACY.md).

This repository contains generic source, synthetic tests, installers and product documentation. The former TikTok project repository remains a private historical archive. It is not a customer installation entry point and must not be made public as part of this repository's publication.

## Maintenance, copyright and licensing

Maintained by [Chuluu](https://github.com/ChuluuMGL). Product website: [YUEYU TECH](https://www.yueyu.tech/zh/products/video-factory/).

Copyright (c) 2026 Chuluu. See [NOTICE](NOTICE) for attribution and authorization status. Original code and documentation use the [MIT license](LICENSE). Dependencies, images and case media retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md). Issues and pull requests are welcome; see [contributing](CONTRIBUTING.md).

Public a31 packages contain the project wheel and hash-locked download metadata. First installation fetches external wheels directly from files.pythonhosted.org; server image registries must also be reachable. Reuse verifies cached bytes and does not redownload unchanged dependencies.
