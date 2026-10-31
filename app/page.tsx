'use client';

/**
 * DocsDrive landing — the 3D workstation is the hero.
 * The isolated Three.js environment (vendored, MIT) is embedded full-screen;
 * the CRT inside it runs the DocsDrive terminal. A minimal fixed bar keeps
 * direct access to the terminal and repo links without blocking the scene.
 */

export default function LandingPage() {
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
                    bottom: 0,
                    left: 0,
                    right: 0,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '10px 20px',
                    background:
                        'linear-gradient(to top, rgba(0,0,0,0.65), rgba(0,0,0,0))',
                    color: '#cdd9e5',
                    fontFamily:
                        'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
                    fontSize: 12,
                    letterSpacing: '0.06em',
                }}
            >
                <span style={{ color: '#7ee787', fontWeight: 700 }}>
                    DOCSDRIVE
                    <span style={{ color: '#6a737d', fontWeight: 400 }}>
                        {' '}
                        · documentation integrity engine
                    </span>
                </span>
                <nav style={{ display: 'flex', gap: 18 }}>
                    <a href="/workstation" style={{ color: '#cdd9e5' }}>
                        workstation
                    </a>
                    <a href="/terminal" style={{ color: '#cdd9e5' }}>
                        terminal
                    </a>
                    <a
                        href="https://github.com/DakshSingh-GitHub/docs-drive"
                        style={{ color: '#cdd9e5' }}
                        target="_blank"
                        rel="noreferrer"
                    >
                        github ↗
                    </a>
                </nav>
            </div>
        </main>
    );
}
