# HACKATHON.md

**Event:** Hacktoberfest 2026 – Hack Day Sonipat (31 Oct – 1 Nov 2026)

## Team

**Team name:** HisenBugs

## Members

1. Jaydev Lamror – GitHub: @jaydevLamror11
2. Rushan Advani – GitHub: @rushanadv
3. Daksh Singh – GitHub: @DakshSingh-GitHub
4. Sahaj Choukhani – GitHub: @sahajchoukhani

## Selected problem statement

**Problem 05: Automated Documentation Drift Detection**

We are building a CI check for GitHub pull requests that finds documentation made incorrect by a code change. It analyzes the PR diff, links changed code (renamed flags, changed defaults, modified parameters, removed features) to the docs that describe it, and reports the exact documentation sentences that are now out of date, each with a reference to the responsible code change.

## AI models used

### gemma4:e4b (Ollama, local)

Open-weight model that fits on a laptop. Used to judge whether a documentation sentence still matches the changed code, and to explain why it does not.

### nomic-embed-text (Ollama, local)

Open-weight embedding model. Used to retrieve the doc passages most related to a changed code symbol, including indirect relationships that exact string matching misses.

Only models that are actually integrated into our code are listed above.