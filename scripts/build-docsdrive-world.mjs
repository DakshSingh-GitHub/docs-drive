#!/usr/bin/env node
/**
 * Builds the DocsDrive 3D workstation world (vendored, MIT — see
 * THIRD_PARTY_NOTICES.md) with its original webpack pipeline and copies the
 * complete output into public/docsdrive-world/ so it is served from the same
 * origin as the DocsDrive Next.js site.
 *
 * Usage: npm run docsdrive:world:build
 */
import { spawnSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const worldRoot = path.join(repoRoot, 'vendor', 'docsdrive-world');
const outDir = path.join(repoRoot, 'public', 'docsdrive-world');

function run(cmd, args, cwd) {
    const res = spawnSync(cmd, args, { cwd, stdio: 'inherit', shell: process.platform === 'win32' });
    if (res.status !== 0) {
        throw new Error(`${cmd} ${args.join(' ')} failed with exit code ${res.status}`);
    }
}

// Ensure dependencies exist in the vendored app
run('npm', ['install', '--no-audit', '--no-fund'], worldRoot);

// Clean previous output, then build
rmSync(outDir, { recursive: true, force: true });
run('npx', ['webpack', '--config', 'bundler/webpack.docsdrive.js'], worldRoot);

console.log('\ndocsdrive-world built → public/docsdrive-world/');
