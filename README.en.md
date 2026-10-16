# Video Factory

> Deploy video workflows to your own server with an Agent Skill.
>
> Feishu Base and n8n by default, with reusable infrastructure for multiple projects.

[中文](README.md) | **English**

[Installation guide](https://www.yueyu.tech/zh/products/video-factory/) · [Pinned release](https://github.com/ChuluuMGL/video-factory/releases) · [Setup Skill](skills/video-factory-setup/SKILL.md) · [Security](SECURITY.md)

## What it does

Video Factory is being built to connect product information, script generation, video generation and human review in a maintainable project workflow. Administrators install and configure it through an AI agent and the CLI; the intended daily team surface is the project's Feishu Base task table.

- **Independent deployment:** customers manage their services, database, accounts and credentials.
- **Multiple projects:** reuse infrastructure while configuring products, Feishu and models per project.
- **Resumable operations:** one Skill supports installation, new projects, interrupted setup and diagnosis.

The installer runs on the customer's server. The Skill guides the AI agent. The public website explains the product and installation; it does not store customer project accounts or model keys.

## Version and availability

**MIT-licensed public alpha. The public Release defines the pinned version and hashes.**

Source, the complete Skill and pinned server packages are publicly readable without GitHub login. Customers provide their own server, Feishu and model accounts.

Cloud regression covers fresh Ubuntu preparation, anonymous pinned-package installation, private-terminal Setup, synthetic Feishu authorization, PostgreSQL, real n8n, cold restore, and disconnected installation with both Docker storage modes. Results apply to the exact source commit checked by Actions.

A development candidate completed one real-account flow in an isolated Base: script return, revision-two approval, one real H3 generation, original-row video writeback and human video approval. The authenticated event and server ledger read back the same revision as `accepted`. **Fresh fixed-release deployment, live script-model generation, a second project, distinct employee permissions and nonauthor installation remain separate acceptance gates.** Historical examples and a successful installation do not replace them.

See the [cloud verification](https://github.com/ChuluuMGL/video-factory/actions/runs/37562989085) and [current status](docs/product/STATUS.md).

## Choose what you want to run

Real local installation and existing n8n integration are under validation in [PR #49](https://github.com/ChuluuMGL/video-factory/pull/49). See the [candidate guide](docs/product/PORTABLE_N8N.md); this is not a stable release.

The Setup Skill first asks where your n8n runs, whether a Feishu application and Base already exist, and where the product execution services should run. Keep an existing n8n instance; choose an installation method only if n8n is absent. Its read-only `scripts/choose_install_path.py` records the route without changing your instance.

| Goal | How to start | Relation to Video Factory |
|---|---|---|
| Use n8n in a browser | Sign up for [n8n Cloud](https://docs.n8n.io/deploy/use-n8n-cloud.md); no local n8n or Docker installation is needed | Standalone n8n, **not** a Video Factory installation |
| Try n8n locally on Windows/Mac | Install Node.js as required by the [official n8n npm guide](https://docs.n8n.io/deploy/host-n8n/install-options/install-with-npm.md), run `npx n8n`, then open `http://localhost:5678`. The upstream guide warns that npm installs are deprecated from n8n 3.0 | Standalone trial; it **does not** install this product |
| Install the full Video Factory stack | Use the Setup instructions below on your own Ubuntu 24.04 x86_64 server; an agent on Windows/Mac can operate it over SSH | Installs product services, PostgreSQL, and the included n8n; deploy a private runner when needed |

The current package **cannot yet integrate** with an existing n8n Cloud or local instance. This is a product gap, not a reason to discard an existing installation. The Ubuntu requirement applies to the **current pinned package's full automated deployment**, not to n8n itself. See the [existing-n8n integration plan](docs/product/EXISTING_N8N_INTEGRATION.md). Read-only inventory is not a completed installation.

## Model availability

| Purpose | Selectable in the pinned release | Development candidate or planned |
|---|---|---|
| Scripts | DeepSeek Flash | GPT, Gemini, MiniMax text, Qwen, and Doubao script routes are in [draft PR #38](https://github.com/ChuluuMGL/video-factory/pull/38); each still needs live-account acceptance |
| Video | MiniMax H3 | Kling, Seedance, and Wan video API adapters are in [draft PR #42](https://github.com/ChuluuMGL/video-factory/pull/42); they are not wired into released Setup or verified with live generations |
| Images | No separate image-generation route | Seedream image generation is tracked in [issue #45](https://github.com/ChuluuMGL/video-factory/issues/45); it is distinct from Seedance video |

Use in an earlier project or passing synthetic tests does not make a provider selectable in the pinned release. Each new route still needs its own account, output writeback, and human-review acceptance. Installation does not itself trigger paid generation.

## Install the full Video Factory stack

Use the [installation guide](https://www.yueyu.tech/zh/products/video-factory/) to download the complete Skill and give it to an agent that can read files and operate your server.

**You can also start directly from GitHub:**

1. Read the [installation and handoff guide](docs/product/CUSTOMER_GUIDE.md).
2. Have your agent read the complete [`skills/video-factory-setup/`](skills/video-factory-setup/) directory, including `references/`.
3. Give it the prompt below. The Skill guides acquisition and verification of the pinned release, followed by interactive setup.

```text
Use the complete Skill in skills/video-factory-setup/. First ask where my n8n
runs, whether my Feishu app and Base exist, and where product services should run.
Preserve existing resources and tell me which route this release can complete.
When supported, configure my first test project. Pin the latest public alpha
Release tag and hashes. Check the environment and package first,
then guide me through Setup. Hide passwords and API keys in a private
terminal on my server; do not collect them in chat.
```

For the current pinned package's **full automated deployment**, prepare an Ubuntu 24.04 x86_64 server with at least 4 GiB RAM, server access, project information, a Feishu application and model accounts. The default CLI installation does not require a business subdomain. The agent checks and prepares Python 3.12, Docker and Compose.

An agent on Windows or macOS connects to that Ubuntu server over SSH. The current release does not deploy its full stack natively on Windows/macOS; this does not prevent standalone use of n8n Cloud or local n8n.

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
| Optional project runner | Customer Docker private network; dispatches approved tasks without a web UI |
| Feishu Base | Project information, source tasks and employees' daily view |
| Interim technical review commands | `stack-feishu-session` authorizes the operator in a private terminal, then imports or reviews one task in the server ledger; this is not employee review in Base |

[Original task-row review](docs/product/FEISHU_NATIVE_REVIEW.md) writes the script, revision and video back to the same task for employee decisions in Feishu Base. Optional [Base result synchronization](docs/product/BASE_RESULTS.md) appends a separate snapshot table. Terminal review is technical acceptance. The default installation does not deploy a persistent employee web interface; see the [product shape and acceptance boundary](docs/product/PRODUCT_SHAPE.md).

## Documentation and repository scope

- Installers: [customer guide](docs/product/CUSTOMER_GUIDE.md), [complete Skill](skills/video-factory-setup/SKILL.md), [acceptance guide](docs/product/INDEPENDENT_ACCEPTANCE.md).
- Maintainers: [troubleshooting and issues](docs/product/TROUBLESHOOTING.en.md), [product definition](docs/product/PRD.md), [status](docs/product/STATUS.md), [migration scope](docs/product/MIGRATION.md), [publication review](docs/product/PUBLICATION_REVIEW.md).
- Security and data: [security reporting](SECURITY.md), [data handling](PRIVACY.md).

This repository contains generic source, synthetic tests, installers and product documentation. The former TikTok project repository remains a private historical archive. It is not a customer installation entry point and must not be made public as part of this repository's publication.

## Maintenance, copyright and licensing

Maintained by [Chuluu](https://github.com/ChuluuMGL). Product website: [YUEYU TECH](https://www.yueyu.tech/zh/products/video-factory/).

Copyright (c) 2026 Chuluu. See [NOTICE](NOTICE) for attribution and authorization status. Original code and documentation use the [MIT license](LICENSE). Dependencies, images and case media retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md). Issues and pull requests are welcome; see [contributing](CONTRIBUTING.md).

Public packages contain the project wheel and hash-locked download metadata. First installation fetches external wheels directly from files.pythonhosted.org; server image registries must also be reachable. Reuse verifies cached bytes and does not redownload unchanged dependencies.
