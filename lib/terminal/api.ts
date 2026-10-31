/**
 * DocsDrive terminal — backend client.
 *
 * Uses ONLY the existing FastAPI endpoints (backend is frozen):
 *   POST /api/analyze-pr, GET /api/runs, GET /api/runs/{id},
 *   GET /api/runs/{id}/findings/{fid}, POST /api/generate-fix,
 *   POST /api/fix/apply, POST /api/analyze-diff,
 *   GET /api/playground/scenarios, GET /api/health
 */

export const API_URL =
    process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export interface RunStatus {
    run_id: string;
    status: 'queued' | 'processing' | 'completed' | 'failed';
    progress: number;
    current_step?: string | null;
    report?: DriftReport | null;
    error_message?: string | null;
}

export interface DriftFinding {
    id: string;
    doc_path: string;
    doc_line_number: number;
    affected_sentence: string;
    code_file_path: string;
    code_line_start: number;
    code_line_end: number;
    code_diff_snippet: string;
    drift_type: string;
    severity: string;
    confidence: number;
    explanation: string;
    suggested_fix: string;
}

export interface DriftReport {
    repo_name: string;
    pr_number?: number | null;
    base_commit: string;
    head_commit: string;
    timestamp: string;
    total_files_analyzed: number;
    total_symbols_changed: number;
    total_doc_sentences_scanned: number;
    findings: DriftFinding[];
    execution_time_seconds: number;
}

export interface GenerateFixResult {
    original_sentence: string;
    suggested_sentence: string;
    suggested_paragraph: string;
    git_patch: string;
    explanation: string;
}

export interface Scenario {
    id: string;
    name: string;
    description: string;
    diff: string;
    docs: Record<string, string>;
    expected_drift: boolean;
}

async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
    const res = await fetch(`${API_URL}${path}`, init);
    if (!res.ok) {
        const detail = await res
            .json()
            .then((d) => (d as { detail?: string }).detail)
            .catch(() => undefined);
        throw new Error(
            `API ${res.status}${detail ? `: ${detail}` : ''}`
        );
    }
    return res.json();
}

export async function getHealth(): Promise<{
    status: string;
    ollama: { available: boolean; models: string[] };
}> {
    return (await fetchJson('/api/health')) as ReturnType<typeof JSON.parse> as {
        status: string;
        ollama: { available: boolean; models: string[] };
    };
}

export async function analyzePr(
    repoUrl: string,
    prNumber: number
): Promise<{ run_id: string; status: string }> {
    return (await fetchJson('/api/analyze-pr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_url: repoUrl, pr_number: prNumber }),
    })) as { run_id: string; status: string };
}

export async function getRun(runId: string): Promise<RunStatus> {
    return (await fetchJson(`/api/runs/${encodeURIComponent(runId)}`)) as RunStatus;
}

export async function listRuns(limit = 20): Promise<{
    runs: Array<Record<string, unknown>>;
    total: number;
}> {
    return (await fetchJson(`/api/runs?limit=${limit}`)) as {
        runs: Array<Record<string, unknown>>;
        total: number;
    };
}

export async function getFinding(
    runId: string,
    findingId: string
): Promise<{ finding: DriftFinding; code_diff_hunk: string; doc_sentence: string; doc_line_number: number }> {
    return (await fetchJson(
        `/api/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(findingId)}`
    )) as {
        finding: DriftFinding;
        code_diff_hunk: string;
        doc_sentence: string;
        doc_line_number: number;
    };
}

export async function generateFix(
    docSentence: string,
    codeDiff: string
): Promise<GenerateFixResult> {
    return (await fetchJson('/api/generate-fix', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            doc_sentence: docSentence,
            code_diff: codeDiff,
            tone: 'concise',
        }),
    })) as GenerateFixResult;
}

export async function getScenarios(): Promise<{ scenarios: Scenario[] }> {
    return (await fetchJson('/api/playground/scenarios')) as {
        scenarios: Scenario[];
    };
}

export async function analyzeDiff(diffText: string, docs: Array<{ path: string; content: string }>): Promise<{
    run_id: string;
    status: string;
    findings_count: number;
    report: DriftReport | null;
}> {
    return (await fetchJson('/api/analyze-diff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ diff_text: diffText, docs }),
    })) as {
        run_id: string;
        status: string;
        findings_count: number;
        report: DriftReport | null;
    };
}
