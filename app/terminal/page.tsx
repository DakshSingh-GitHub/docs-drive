'use client';

import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import Terminal from './Terminal';

export default function TerminalPage() {
    return (
        <Suspense
            fallback={
                <div
                    style={{
                        background: '#0c1210',
                        minHeight: '100vh',
                        color: '#7ee787',
                        fontFamily: 'monospace',
                        padding: 20,
                    }}
                >
                    DOCSDRIVE TERMINAL — BOOTING…
                </div>
            }
        >
            <TerminalInner />
        </Suspense>
    );
}

function TerminalInner() {
    const params = useSearchParams();
    const embed = params.get('embed') === '1';
    return <Terminal embed={embed} />;
}
