/**
 * Bridge between the DocsDrive terminal (running inside the CRT iframe) and
 * the outer 3D world.
 *
 * The outer world's MonitorScreen listens for `window.message` events with
 * data.type of 'mousemove' | 'keydown' | 'keyup' and re-dispatches them as
 * CustomEvents on the iframe element (with inComputer=true) so the camera
 * reacts to interaction inside the screen. clientX/clientY must be relative
 * to the iframe's own viewport.
 *
 * Protocol (same-origin only):
 *   iframe → parent: { type: 'mousemove', clientX, clientY }
 *                    { type: 'keydown'|'keyup', key }
 *                    { type: 'DOCSDRIVE_TERMINAL_READY' }
 */

export function installTerminalBridge(): void {
    if (typeof window === 'undefined') return;
    if ((window as unknown as { __docsDriveBridge?: boolean }).__docsDriveBridge)
        return;
    (window as unknown as { __docsDriveBridge?: boolean }).__docsDriveBridge =
        true;

    const send = (payload: Record<string, unknown>) => {
        if (window.parent && window.parent !== window) {
            window.parent.postMessage(payload, window.location.origin);
        }
    };

    window.addEventListener(
        'mousemove',
        (e) => {
            const { clientX, clientY } = e as MouseEvent;
            send({ type: 'mousemove', clientX, clientY });
        },
        { passive: true }
    );

    window.addEventListener('keydown', (e) => {
        send({ type: 'keydown', key: e.key });
    });

    window.addEventListener('keyup', (e) => {
        send({ type: 'keyup', key: e.key });
    });

    // Tell the outer world the terminal is ready to receive interaction
    window.addEventListener('load', () => {
        send({ type: 'DOCSDRIVE_TERMINAL_READY' });
    });
}
