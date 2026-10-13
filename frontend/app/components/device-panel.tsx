'use client';
import { ArrowLeft, Circle, Expand, Radio, RefreshCw, Smartphone, Square } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { StudioState } from './use-studio';

/**
 * Local device screen viewer — replaces the MobileRun DeviceStream component.
 *
 * Periodically fetches screenshots from the FastAPI backend and displays them
 * as an auto-refreshing image.  When the agent is running, the refresh rate
 * increases to show near-real-time feedback.
 */
function LocalDeviceScreen({
  busy,
  onConnected,
}: {
  busy: boolean;
  onConnected: (connected: boolean) => void;
}) {
  const [src, setSrc] = useState('');
  const [error, setError] = useState(false);
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch('/api/studio/device/screenshot', { cache: 'no-store' });
      if (!response.ok) throw new Error('Screenshot failed');
      const blob = await response.blob();
      if (mountedRef.current) {
        const url = URL.createObjectURL(blob);
        setSrc((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
        setError(false);
        onConnected(true);
      }
    } catch {
      if (mountedRef.current) {
        setError(true);
        onConnected(false);
      }
    }
  }, [onConnected]);

  useEffect(() => {
    mountedRef.current = true;
    // Initial load.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
    // Refresh rate: faster when agent is active.
    const interval = setInterval(() => void refresh(), busy ? 1500 : 5000);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [busy, refresh]);

  useEffect(() => {
    return () => {
      if (src) URL.revokeObjectURL(src);
    };
  }, [src]);

  if (error || !src) {
    return (
      <div
        className="stream-placeholder"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          opacity: 0.5,
          fontSize: '0.85rem',
        }}
      >
        <Smartphone size={20} style={{ marginRight: 8 }} />
        {error ? 'Waiting for device…' : 'Connecting…'}
      </div>
    );
  }

  return (
    <img
      src={src}
      alt="Device screen"
      style={{ width: '100%', height: '100%', objectFit: 'contain' }}
      draggable={false}
    />
  );
}

export function DevicePanel({ state }: { state: StudioState }) {
  const { device, phoneStage, deviceError, busy, loadDevice, setConnected } = state;
  const [localConnected, setLocalConnected] = useState(false);

  const handleConnected = useCallback(
    (connected: boolean) => {
      setLocalConnected(connected);
      setConnected(connected);
    },
    [setConnected],
  );

  return (
    <section className="device-column">
      <div className="device-heading">
        <div>
          <span className="section-index">02</span>
          <h2>Your live device</h2>
        </div>
        <span className={`connection-badge ${localConnected ? 'connected' : ''}`}>
          <Radio size={12} />
          {localConnected ? 'LIVE' : deviceError ? 'OFFLINE' : 'CONNECTING'}
        </span>
      </div>
      <div className="device-stage" ref={phoneStage}>
        <div className="stage-label">
          <Smartphone size={13} />
          <span>{device?.name || 'Your phone'}</span>
        </div>
        <button
          className="expand-button"
          title="Expand device view"
          aria-label="Expand device view"
          onClick={() => {
            if (document.fullscreenElement) void document.exitFullscreen();
            else void phoneStage.current?.requestFullscreen();
          }}
        >
          <Expand size={15} />
        </button>
        <div className="stage-grid" />
        <div className="phone-frame">
          <div className="phone-screen">
            {deviceError ? (
              <div className="stream-error">
                <Smartphone size={28} />
                <h3>Connection interrupted</h3>
                <p>{deviceError}</p>
                <button onClick={() => void loadDevice()}>
                  <RefreshCw size={13} />
                  Reconnect
                </button>
              </div>
            ) : (
              <LocalDeviceScreen busy={busy} onConnected={handleConnected} />
            )}
          </div>
        </div>
        <div className="device-navigation">
          <button aria-label="Android back" title="Back" disabled={!localConnected || busy}>
            <ArrowLeft size={17} />
          </button>
          <button aria-label="Android home" title="Home" disabled={!localConnected || busy}>
            <Circle size={15} />
          </button>
          <button
            aria-label="Android recent apps"
            title="Recent apps"
            disabled={!localConnected || busy}
          >
            <Square size={13} />
          </button>
          <span />
          <button aria-label="Reconnect stream" title="Reconnect" onClick={() => void loadDevice()}>
            <RefreshCw size={14} />
          </button>
        </div>
        <div className="stage-caption">
          <i />
          {busy ? 'Agent in control' : 'Screenshot view'}
          <span>ANDROID · LOCAL ADB</span>
        </div>
      </div>
    </section>
  );
}
