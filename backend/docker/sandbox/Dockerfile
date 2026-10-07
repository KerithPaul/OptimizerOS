# ArchitectOS sandbox image.
#
# Used exclusively by backend/app/changes/sandbox.py (run_in_sandbox) to
# run install/build/lint/typecheck/unit_test for a Code Agent's patch,
# for any connected project's repository. SANDBOX_IMAGE is a single
# global setting (backend/app/core/config.py) — there is no
# per-repository override — so this image must carry every toolchain
# `backend/app/changes/commands.py` can emit a command for. Add a
# stack's tools here as new project types get connected; do not swap
# SANDBOX_IMAGE per project.

FROM node:20-bookworm

# Alternate JS package managers referenced by commands.py's _NODE_INSTALL
# (pnpm, yarn). npm ships with the base image already. bun is not managed
# by corepack/npm and would need its own install step if a connected repo
# uses it.
#
# Installed as real global npm packages, NOT via `corepack enable`/`corepack
# prepare`. Corepack shims fetch `@pnpm/exe.*` on every invocation.
# Projects may still pin an older pnpm via package.json `packageManager`
# (lex-fintech: pnpm@10.4.1). sandbox.py lets that download happen during
# install (network on) onto the per-repo `/cache` volume, then lint/test
# reuse it offline. Do not set pmOnFail=ignore: pnpm 12 then skips
# package.json `pnpm.overrides` and frozen install fails with
# ERR_PNPM_LOCKFILE_CONFIG_MISMATCH. Remove the base image's corepack
# shims first — they pre-occupy /usr/local/bin/{pnpm,yarn,...} and make
# `npm install -g` fail with EEXIST.
RUN rm -f /usr/local/bin/pnpm /usr/local/bin/pnpx /usr/local/bin/yarn /usr/local/bin/yarnpkg \
    && npm install -g pnpm@12.4.2 yarn@1.22.22

# Python toolchain, for projects whose architecture_profile.language == "python".
# Debian 12 marks the system Python as externally managed (PEP 668); the
# sandbox always runs as a throwaway container, so --break-system-packages
# is fine here (never done on a host install).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --break-system-packages --no-cache-dir uv poetry ruff pytest

WORKDIR /workspace
