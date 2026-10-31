'use client';

/**
 * /workstation — the DocsDrive 3D workstation.
 *
 * Embeds the isolated Three.js environment (vendored, MIT — see
 * THIRD_PARTY_NOTICES.md) served from /docsdrive-world/ on the same origin.
 * The world renders its own loading/BIOS screen, WebGL fallback and camera
 * choreography; inside the CRT it loads /terminal?embed=1.
 */

import { useEffect, useState } from 'react';

export default function WorkstationPage() {
    const [ready, setReady] = useState(false);

    useEffect(() => {
        const t = setTimeout(() => setReady(true), 1200);
        return () => clearTimeout(t);
    }, []);

    return (
        <main
            style={{
                position: 'fixed',
                inset: 0,
                background: '#000',
                overflow: 'hidden',
            }}
        >
            <iframe
                src="/docsdrive-world/index.html"
                title="DocsDrive Workstation"
                allow="autoplay"
                style={{
                    position: 'absolute',
                    inset: 0,
                    width: '100%',
                    height: '100%',
                    border: 'none',
                }}
            />
            <div
                style={{
                    position: 'absolute',
                    top: 16,
                    left: 20,
                    right: 20,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    color: '#7ee787',
                    fontFamily:
                        'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
                    fontSize: 13,
                    letterSpacing: '0.08em',
                    pointerEvents: 'none',
                    opacity: ready ? 0 : 1,
                    transition: 'opacity 0.8s ease',
                    textShadow: '0 1px 4px rgba(0,0,0,0.8)',
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <img
                        src="/icon.svg"
                        alt="DocsDrive Logo"
                        width={22}
                        height={22}
                        style={{
                            borderRadius: 6,
                            display: 'inline-block',
                            boxShadow: '0 0 10px rgba(0, 240, 138, 0.35)',
                        }}
                    />
                    <span style={{ fontWeight: 700 }}>DOCSDRIVE</span>
                </div>
                <span>BOOTING DOCUMENTATION INTEGRITY ENGINE…</span>
            </div>
        </main>
    );
}
