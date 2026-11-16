---
title: Build your family tree with your own AI agent (beta)
nav: none
description: Point an AI agent that runs on your own computer at opengenealogyai.org/llms.txt and it builds your family tree in one database file on your computer. Beta.
---
<p class="eyebrow"><span class="badge beta">Beta</span> For the person, not the agent</p>
# Build your family tree with your own AI agent

<div class="beta-note">
<p><strong>Beta.</strong> The kit uploads nothing to us and creates no account. Your own AI agent still sends what it reads to its model provider, as every AI assistant does. A project folder inside OneDrive, iCloud or Dropbox will sync the tree, living relatives included, to that service.</p>
</div>

## What this is

Point your AI agent at `opengenealogyai.org/llms.txt` and tell it to build your family tree. It will set up three roles — the Orchestrator talks to you, the Genealogist does the research, Quality Control checks every finding — and keep everything in one database file on your computer. It also writes a page in that folder that draws the tree as a chart, plus a shareable copy that leaves out living people, and a small program that shows the same tree live on your own computer, for you only — where an adopted person can follow both their birth line and their adoptive line.

## What to tell your agent

> Read https://opengenealogyai.org/llms.txt and follow /agents/START.md exactly.

## Which agents it is written for

An agent that runs on your own computer and can write files and run Python there: Claude Code and similar local tools. An agent that runs in a remote sandbox (Grok's code tools, Codex in the cloud, the code box in a chat window) cannot put files on your machine, and must not build your tree elsewhere. A phone or tablet alone cannot run this; it needs a computer with Python. A plain chat assistant should use the [starter prompt](/onboarding/STARTER_PROMPT.md) instead.

## What you will be asked to do

Name a folder; answer one question at a time; log in to your own genealogy sites when asked; make a few decisions on your own page. Never give a password to the agent.

## Honest limits

1. **Living people.** The kit refuses to store a living person without a private licence, but it cannot stop an agent sending text to an outside service first, so tell your agent which sites are allowed.
2. **Quality Control** catches invented sources and some wrong attributions, not every error.
3. **One agent doing everything** runs Quality Control as the same model as the Genealogist, which is a weaker check.

## The files

Agents download these with a program, so each file is served exactly as written, with its SHA-256 fingerprint so the download can be checked. The same list is in [SHA256SUMS.txt](/agents/SHA256SUMS.txt) and [manifest.json](/agents/manifest.json).

<!--KIT-->

The four scripts are MIT-licensed ([licence](/agents/LICENSE)); the MAXGEN standard itself is [CC0](/license/). The two are not the same thing.
