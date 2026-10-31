'use client';

/**
 * DocsDrive Terminal — a real interactive terminal designed for the CRT in
 * the 3D workstation, also usable standalone at /terminal.
 *
 * In embed mode it installs the postMessage bridge so the outer 3D camera
 * reacts to mouse/keyboard interaction inside the screen.
 */

import React, {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
} from 'react';
import './terminal.css';
import { installTerminalBridge } from '../../lib/terminal/bridge';
import * as api from '../../lib/terminal/api';

type LineClass =
    | 'tl-dim'
    | 'tl-green'
    | 'tl-bright'
    | 'tl-amber'
    | 'tl-red'
    | 'tl-white'
    | 'tl-blue'
    | '';

interface Line {
    text: string;
    cls: LineClass;
}

const BIOS_LINES: Array<{ text: string; delay: number; cls?: LineClass }> = [
    { text: 'DOCSDRIVE SYSTEMS', delay: 120 },
    { text: 'DOCSDRIVE BIOS — DOCUMENTATION INTEGRITY ENGINE', delay: 90 },
    { text: 'Copyright (C) 2026 DocsDrive Systems Inc.', delay: 90, cls: 'tl-dim' },
    { text: '', delay: 80 },
    { text: 'DDBIOS v1.0 — INITIALISING ENVIRONMENT', delay: 140 },
    { text: 'Checking RAM : 40960K OK', delay: 100, cls: 'tl-dim' },
    { text: 'LOADING ANALYSIS MODULES .......... OK', delay: 130 },
    { text: 'MOUNTING DOCUMENT LAYERS ........... OK', delay: 110 },
    { text: 'REPOSITORY CONNECTION READY', delay: 150 },
    { text: '', delay: 80 },
    { text: 'SYSTEM READY', delay: 200, cls: 'tl-bright' },
    { text: '', delay: 150 },
];

const HELP_LINES: Array<[string, string]> = [
    ['help', 'list available commands'],
    ['about', 'what DocsDrive is'],
    ['health', 'backend + local model status'],
    ['analyze-pr <repo_url> <pr>', 'analyze a GitHub pull request for doc drift'],
    ['runs [n]', 'list recent analysis runs'],
    ['finding <run_id> <finding_id>', 'inspect one finding in detail'],
    ['fix <run_id> <finding_id>', 'generate an AI-suggested doc fix'],
    ['scenarios', 'list built-in demo scenarios'],
    ['demo <scenario_id>', 'run a demo scenario through the pipeline'],
    ['clear', 'clear the screen'],
];

function severityColor(sev: string): LineClass {
    switch (sev) {
        case 'critical':
        case 'high':
            return 'tl-red';
        case 'medium':
            return 'tl-amber';
        default:
            return 'tl-blue';
    }
}

export default function Terminal({ embed }: { embed: boolean }) {
    const [booted, setBooted] = useState(false);
    const [biosIndex, setBiosIndex] = useState(0);
    const [lines, setLines] = useState<Line[]>([]);
    const [input, setInput] = useState('');
    const [history, setHistory] = useState<string[]>([]);
    const [historyIdx, setHistoryIdx] = useState(-1);
    const [busy, setBusy] = useState(false);
    const outputRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const linesRef = useRef<Line[]>([]);
    linesRef.current = lines;

    const push = useCallback((text: string, cls: LineClass = '') => {
        setLines((prev) => [...prev, { text, cls }]);
    }, []);

    const pushMany = useCallback((entries: Array<[string, LineClass?]>) => {
        setLines((prev) => [
            ...prev,
            ...entries.map(([text, cls]) => ({ text, cls: cls ?? ('' as LineClass) })),
        ]);
    }, []);

    // ---- embed bridge ----
    useEffect(() => {
        if (embed) installTerminalBridge();
    }, [embed]);

    // ---- BIOS boot ----
    useEffect(() => {
        if (!booted && biosIndex < BIOS_LINES.length) {
            const entry = BIOS_LINES[biosIndex];
            const t = setTimeout(() => setBiosIndex((i) => i + 1), entry.delay);
            return () => clearTimeout(t);
        }
        if (!booted && biosIndex >= BIOS_LINES.length) {
            const t = setTimeout(() => setBooted(true), 350);
            return () => clearTimeout(t);
        }
    }, [booted, biosIndex]);

    const skipBoot = useCallback(() => {
        setBiosIndex(BIOS_LINES.length);
    }, []);

    useEffect(() => {
        if (booted) inputRef.current?.focus();
    }, [booted]);

    useEffect(() => {
        const el = outputRef.current;
        if (el) el.scrollTop = el.scrollHeight;
    }, [lines, busy]);

    // ---- command execution ----
    const runCommand = useCallback(
        async (raw: string) => {
            const cmdline = raw.trim();
            push(`docsdrive:~$ ${cmdline}`, 'tl-white');
            if (!cmdline) return;
            setHistory((h) => [cmdline, ...h].slice(0, 100));
            setHistoryIdx(-1);

            const [cmd, ...args] = cmdline.split(/\s+/);
            setBusy(true);
            try {
                switch (cmd) {
                    case 'help': {
                        push('DOCSDRIVE TERMINAL — commands', 'tl-bright');
                        for (const [name, desc] of HELP_LINES) {
                            push(
                                `  ${name.padEnd(34, ' ')}${desc}`,
                                'tl-dim'
                            );
                        }
                        break;
                    }
                    case 'clear': {
                        setLines([]);
                        break;
                    }
                    case 'about': {
                        pushMany([
                            [
                                'DocsDrive — documentation integrity engine.',
                                'tl-bright',
                            ],
                            [
                                'It reads the code changes in a pull request, finds the',
                                '',
                            ],
                            [
                                'documentation those changes silently made wrong, and shows',
                                '',
                            ],
                            [
                                'the exact doc sentence next to the responsible code diff.',
                                '',
                            ],
                            [
                                'Verification runs on local open-weight models (gemma4:e4b',
                                'tl-dim',
                            ],
                            [
                                '+ nomic-embed-text via Ollama) — nothing leaves your laptop.',
                                'tl-dim',
                            ],
                        ]);
                        break;
                    }
                    case 'health': {
                        try {
                            const h = await api.getHealth();
                            push(`backend : ${h.status}`, 'tl-green');
                            push(
                                `ollama  : ${h.ollama.available ? 'online' : 'offline'}${
                                    h.ollama.models?.length
                                        ? ` (${h.ollama.models.join(', ')})`
                                        : ''
                                }`,
                                h.ollama.available ? 'tl-green' : 'tl-amber'
                            );
                        } catch (e) {
                            push(
                                `backend unreachable at ${api.API_URL} — start it with: cd backend && uvicorn api.main:app --port 8000`,
                                'tl-red'
                            );
                        }
                        break;
                    }
                    case 'analyze-pr': {
                        if (args.length < 2) {
                            push(
                                'usage: analyze-pr <github_repo_url> <pr_number>',
                                'tl-amber'
                            );
                            break;
                        }
                        const repoUrl = args[0];
                        const prNumber = parseInt(args[1], 10);
                        if (!Number.isFinite(prNumber)) {
                            push('pr number must be an integer', 'tl-amber');
                            break;
                        }
                        try {
                            const { run_id, status } = await api.analyzePr(
                                repoUrl,
                                prNumber
                            );
                            push(
                                `run ${run_id} ${status} — polling for progress…`,
                                'tl-dim'
                            );
                            const done = await pollRun(run_id);
                            if (done.report) renderReport(done.report, push);
                        } catch (e) {
                            push(`analysis failed: ${(e as Error).message}`, 'tl-red');
                        }
                        break;
                    }
                    case 'runs': {
                        try {
                            const limit = Number.isFinite(parseInt(args[0], 10))
                                ? parseInt(args[0], 10)
                                : 10;
                            const { runs, total } = await api.listRuns(limit);
                            push(`recent runs (${total} total):`, 'tl-bright');
                            if (!runs.length) push('  (none yet)', 'tl-dim');
                            for (const r of runs) {
                                const rid = String(r.run_id ?? '');
                                push(
                                    `  ${rid}  ${String(r.status ?? '')}`.padEnd(
                                        40
                                    ) + String(r.created_at ?? ''),
                                    'tl-dim'
                                );
                            }
                        } catch (e) {
                            push(
                                `backend unreachable at ${api.API_URL}`,
                                'tl-red'
                            );
                        }
                        break;
                    }
                    case 'finding': {
                        if (args.length < 2) {
                            push(
                                'usage: finding <run_id> <finding_id>',
                                'tl-amber'
                            );
                            break;
                        }
                        try {
                            const d = await api.getFinding(args[0], args[1]);
                            renderFinding(d, push);
                        } catch (e) {
                            push(`not found: ${(e as Error).message}`, 'tl-red');
                        }
                        break;
                    }
                    case 'fix': {
                        if (args.length < 2) {
                            push(
                                'usage: fix <run_id> <finding_id>',
                                'tl-amber'
                            );
                            break;
                        }
                        try {
                            push('fetching finding…', 'tl-dim');
                            const d = await api.getFinding(args[0], args[1]);
                            push('asking local gemma4:e4b for a rewrite…', 'tl-dim');
                            const fix = await api.generateFix(
                                d.doc_sentence,
                                d.code_diff_hunk
                            );
                            push('original  :', 'tl-dim');
                            push(`  ${fix.original_sentence}`, 'tl-red');
                            push('suggested :', 'tl-dim');
                            push(`  ${fix.suggested_sentence}`, 'tl-green');
                            push('explanation:', 'tl-dim');
                            push(`  ${fix.explanation}`, 'tl-white');
                            push('git patch:', 'tl-dim');
                            push(fix.git_patch, 'tl-dim');
                        } catch (e) {
                            push(`fix failed: ${(e as Error).message}`, 'tl-red');
                        }
                        break;
                    }
                    case 'scenarios': {
                        try {
                            const { scenarios } = await api.getScenarios();
                            push('demo scenarios:', 'tl-bright');
                            for (const s of scenarios) {
                                push(
                                    `  ${s.id.padEnd(28)}${s.name}`,
                                    'tl-green'
                                );
                                push(`    ${s.description}`, 'tl-dim');
                            }
                        } catch (e) {
                            push(
                                `backend unreachable at ${api.API_URL}`,
                                'tl-red'
                            );
                        }
                        break;
                    }
                    case 'demo': {
                        if (!args[0]) {
                            push('usage: demo <scenario_id>  (try: scenarios)', 'tl-amber');
                            break;
                        }
                        try {
                            const { scenarios } = await api.getScenarios();
                            const s = scenarios.find(
                                (x) => x.id === args[0] || x.id === args[0].replace(/^0*/, '')
                            );
                            if (!s) {
                                push(`unknown scenario '${args[0]}'`, 'tl-red');
                                break;
                            }
                            push(`running scenario: ${s.name}`, 'tl-bright');
                            const res = await api.analyzeDiff(
                                s.diff,
                                Object.entries(s.docs).map(([path, content]) => ({
                                    path,
                                    content,
                                }))
                            );
                            push(
                                `${res.findings_count} finding(s) — expected drift: ${s.expected_drift}`,
                                res.findings_count > 0 === s.expected_drift
                                    ? 'tl-green'
                                    : 'tl-amber'
                            );
                            if (res.report) renderReport(res.report, push);
                        } catch (e) {
                            push(`demo failed: ${(e as Error).message}`, 'tl-red');
                        }
                        break;
                    }
                    default:
                        push(
                            `unknown command '${cmd}' — try 'help'`,
                            'tl-amber'
                        );
                }
            } finally {
                setBusy(false);
                requestAnimationFrame(() => inputRef.current?.focus());
            }
        },
        [push, pushMany]
    );

    const onKeyDown = useCallback(
        (e: React.KeyboardEvent<HTMLInputElement>) => {
            if (e.key === 'Enter' && !busy) {
                const value = input;
                setInput('');
                void runCommand(value);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                const next = Math.min(historyIdx + 1, history.length - 1);
                if (next >= 0) {
                    setHistoryIdx(next);
                    setInput(history[next] ?? '');
                }
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                const next = historyIdx - 1;
                setHistoryIdx(next);
                setInput(next >= 0 ? history[next] ?? '' : '');
            } else if (e.key === 'Escape') {
                skipBoot();
            }
        },
        [busy, input, history, historyIdx, runCommand, skipBoot]
    );

    const greeting = useMemo(
        () => [
            ['', ''] as [string, LineClass?],
            [
                'DOCSDRIVE TERMINAL v1.0 — documentation integrity engine',
                'tl-bright',
            ] as [string, LineClass?],
            [
                'Docs that code silently broke, found. Type help for commands.',
                'tl-dim',
            ] as [string, LineClass?],
        ],
        []
    );

    return (
        <div
            className={`terminal-root${embed ? '' : ' terminal-standalone'}`}
            onMouseDown={() => inputRef.current?.focus()}
        >
            <div className="terminal-scanlines" />
            <div className="terminal-header">
                <span className="terminal-brand">DOCSDRIVE TERMINAL</span>
                <span>DOCUMENTATION INTEGRITY ENGINE</span>
                {!embed && (
                    <a className="terminal-backlink" href="/workstation">
                        ↩ workstation
                    </a>
                )}
            </div>

            {!booted ? (
                <div
                    className="terminal-bios"
                    onClick={skipBoot}
                    role="button"
                    aria-label="Skip BIOS boot"
                >
                    {BIOS_LINES.slice(0, biosIndex).map((l, i) => (
                        <div key={i} className={l.cls ?? ''}>
                            {l.text || '\u00a0'}
                        </div>
                    ))}
                    <span className="tl-green">█</span>
                </div>
            ) : (
                <>
                    <div className="terminal-output" ref={outputRef}>
                        {lines.length === 0 &&
                            greeting.map(([t, c], i) => (
                                <div key={i} className={c ?? ''}>
                                    {t || '\u00a0'}
                                </div>
                            ))}
                        {lines.map((l, i) => (
                            <div key={i} className={l.cls}>
                                {l.text || '\u00a0'}
                            </div>
                        ))}
                        {busy && <div className="tl-dim">▌ working…</div>}
                    </div>
                    <div className="terminal-input-row">
                        <span className="terminal-prompt">docsdrive:~$</span>
                        <input
                            ref={inputRef}
                            className="terminal-input"
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyDown={onKeyDown}
                            spellCheck={false}
                            autoComplete="off"
                            aria-label="Terminal command input"
                        />
                    </div>
                </>
            )}
        </div>
    );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

async function pollRun(runId: string): Promise<api.RunStatus> {
    for (let i = 0; i < 600; i++) {
        const run = await api.getRun(runId);
        if (run.status === 'completed') return run;
        if (run.status === 'failed')
            throw new Error(run.error_message || 'run failed');
        await new Promise((r) => setTimeout(r, 1500));
    }
    throw new Error('timed out waiting for run to complete');
}

function renderReport(
    report: api.DriftReport,
    push: (text: string, cls?: LineClass) => void
) {
    push(
        `report ${report.repo_name}${
            report.pr_number ? ` PR#${report.pr_number}` : ''
        } — ${report.total_files_analyzed} files, ${report.total_symbols_changed} code changes, ${report.total_doc_sentences_scanned} doc sentences scanned (${report.execution_time_seconds.toFixed(1)}s)`,
        'tl-dim'
    );
    if (!report.findings.length) {
        push('✓ no documentation drift detected', 'tl-green');
        return;
    }
    for (const f of report.findings) {
        push(
            `■ ${f.drift_type} · ${f.severity.toUpperCase()} · confidence ${(f.confidence * 100).toFixed(0)}%`,
            severityColor(f.severity)
        );
        push(`  doc : ${f.doc_path}:${f.doc_line_number}`, 'tl-dim');
        push(`  “${f.affected_sentence}”`, 'tl-white');
        push(
            `  code: ${f.code_file_path}:${f.code_line_start}-${f.code_line_end}`,
            'tl-dim'
        );
        push(`  ${f.explanation}`, '');
        push(`  run: finding ${f.id}`, 'tl-blue');
    }
}

function renderFinding(
    d: {
        finding: api.DriftFinding;
        code_diff_hunk: string;
        doc_sentence: string;
        doc_line_number: number;
    },
    push: (text: string, cls?: LineClass) => void
) {
    const f = d.finding;
    push(
        `FINDING ${f.id} — ${f.drift_type} · ${f.severity} · ${(f.confidence * 100).toFixed(0)}% confidence`,
        severityColor(f.severity)
    );
    push('', '');
    push('DOC', 'tl-bright');
    push(`${f.doc_path}:${d.doc_line_number}`, 'tl-dim');
    push(`  ${d.doc_sentence}`, 'tl-red');
    push('', '');
    push('CODE', 'tl-bright');
    push(`${f.code_file_path}:${f.code_line_start}-${f.code_line_end}`, 'tl-dim');
    for (const l of d.code_diff_hunk.split('\n').slice(0, 14)) {
        push(`  ${l}`, l.startsWith('+') ? 'tl-green' : l.startsWith('-') ? 'tl-red' : 'tl-dim');
    }
    push('', '');
    push('WHY', 'tl-bright');
    push(`  ${f.explanation}`, 'tl-white');
    push(`suggested fix: ${f.suggested_fix}`, 'tl-dim');
    push(`generate a rewrite with: fix ${f.id.slice(0, 0)}<run_id> ${f.id}`, 'tl-blue');
}
