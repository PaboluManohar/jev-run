'use client';
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Circle,
  Expand,
  Loader2,
  Play,
  Radio,
  RefreshCw,
  Smartphone,
  Square,
  Zap,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { actionDetail, buildStreamUrl, statusLabels } from './studio-types';
import type { StudioState } from './use-studio';

/**
 * Fallback local device screen viewer — fetches screenshots from FastAPI.
 */
function LocalDeviceScreen({
  udid,
  busy,
  onConnected,
}: {
  udid?: string;
  busy: boolean;
  onConnected: (connected: boolean) => void;
}) {
  const [src, setSrc] = useState('');
  const [error, setError] = useState(false);
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const url = udid
        ? `/api/studio/device/screenshot?udid=${encodeURIComponent(udid)}`
        : '/api/studio/device/screenshot';
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) throw new Error('Screenshot failed');
      const blob = await response.blob();
      if (mountedRef.current) {
        const objectUrl = URL.createObjectURL(blob);
        setSrc((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return objectUrl;
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
  }, [udid, onConnected]);

  useEffect(() => {
    mountedRef.current = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
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
        {error ? 'Waiting for device…' : 'Connecting screenshot…'}
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

/**
 * Live device stream embedded inside the phone screen via ws-scrcpy iframe.
 */
function LiveDeviceStream({
  udid,
  busy,
  onConnected,
}: {
  udid: string;
  busy: boolean;
  onConnected: (connected: boolean) => void;
}) {
  const [iframeError, setIframeError] = useState(false);
  const [useFallback, setUseFallback] = useState(false);

  const streamUrl = useMemo(() => {
    if (!udid) return '';
    return buildStreamUrl(udid);
  }, [udid]);

  if (useFallback || iframeError) {
    return (
      <div style={{ position: 'relative', width: '100%', height: '100%' }}>
        <LocalDeviceScreen udid={udid} busy={busy} onConnected={onConnected} />
        <button
          onClick={() => {
            setIframeError(false);
            setUseFallback(false);
          }}
          className="stream-toggle-button"
          title="Switch back to live stream"
        >
          <Radio size={11} />
          Switch to Stream
        </button>
      </div>
    );
  }

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', background: '#000' }}>
      <iframe
        key={udid}
        src={streamUrl}
        title={`Device stream: ${udid}`}
        className="stream-iframe"
        allow="autoplay; fullscreen"
        onLoad={() => onConnected(true)}
        onError={() => {
          setIframeError(true);
          onConnected(false);
        }}
        style={{
          width: '100%',
          height: '100%',
          border: 'none',
          display: 'block',
          borderRadius: 'inherit',
        }}
      />
      <button
        onClick={() => setUseFallback(true)}
        className="stream-toggle-button"
        title="Switch to screenshot view"
      >
        Snapshot mode
      </button>
    </div>
  );
}

export function DevicePanel({ state }: { state: StudioState }) {
  const {
    device,
    devices,
    selectedDeviceId,
    loadDevices,
    phoneStage,
    deviceError,
    busy,
    loadDevice,
    setConnected,
    goal,
    setGoal,
    maxSteps,
    setMaxSteps,
    start,
    stop,
    submitting,
    activeAnywhere,
    run,
    error,
    actions,
    latest,
  } = state;

  const [localConnected, setLocalConnected] = useState(false);
  const [quickGoal, setQuickGoal] = useState('');
  const [quickSteps, setQuickSteps] = useState(maxSteps || 30);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (maxSteps) setQuickSteps(maxSteps);
  }, [maxSteps]);

  // Sync quick goal with state goal initially or when goal changes from suggestions
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (goal) setQuickGoal(goal);
  }, [goal]);

  const handleConnected = useCallback(
    (connected: boolean) => {
      setLocalConnected(connected);
      setConnected(connected);
    },
    [setConnected],
  );

  const activeUDID = selectedDeviceId || device?.id || '';
  const activeDevice = devices.find((d) => d.id === activeUDID) || device;

  const handleQuickRun = async () => {
    const prompt = quickGoal.trim() || goal.trim();
    if (!prompt || busy || submitting) return;
    await start(prompt, quickSteps);
  };

  const statusLabel = run?.status ? statusLabels[run.status] || run.status : '';

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

      {/* Device Stage with Stream */}
      <div className="device-stage" ref={phoneStage}>
        <div className="stage-label">
          <Smartphone size={13} />
          <span>{activeDevice?.name ? `${activeDevice.name} (${activeUDID})` : 'Your phone'}</span>
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
                <button
                  onClick={() => {
                    void loadDevices();
                    void loadDevice();
                  }}
                >
                  <RefreshCw size={13} />
                  Reconnect
                </button>
              </div>
            ) : activeUDID ? (
              <LiveDeviceStream udid={activeUDID} busy={busy} onConnected={handleConnected} />
            ) : (
              <LocalDeviceScreen udid={activeUDID} busy={busy} onConnected={handleConnected} />
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
          <button
            aria-label="Reconnect stream"
            title="Reconnect"
            onClick={() => {
              void loadDevices();
              void loadDevice();
            }}
          >
            <RefreshCw size={14} />
          </button>
        </div>
        <div className="stage-caption">
          <i />
          {busy
            ? 'Agent in control'
            : localConnected
              ? 'ws-scrcpy live stream'
              : 'Connecting stream'}
          <span>{activeUDID ? `UDID: ${activeUDID}` : 'ANDROID · LOCAL ADB'}</span>
        </div>
      </div>

      {/* Autonomous Task Execution Panel */}
      <div className="quick-task-card">
        <div className="quick-task-header">
          <div className="quick-task-title">
            <Zap size={14} fill="currentColor" />
            <span>Autonomous Task Execution</span>
          </div>
          {activeUDID && (
            <span className="quick-task-target" title="Target device UDID">
              UDID: <code>{activeUDID}</code>
            </span>
          )}
        </div>

        <div className="quick-task-input-row">
          <textarea
            className="quick-task-textarea"
            placeholder={
              activeUDID
                ? `Enter task for ${activeDevice?.name || activeUDID}…`
                : 'Select an Android device to execute task…'
            }
            value={quickGoal}
            onChange={(e) => {
              setQuickGoal(e.target.value);
              setGoal(e.target.value);
            }}
            disabled={busy || !activeUDID}
            rows={2}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault();
                void handleQuickRun();
              }
            }}
          />
        </div>

        <div className="quick-task-actions">
          <label className="quick-step-select">
            <span>Steps:</span>
            <select
              value={quickSteps}
              disabled={busy}
              onChange={(e) => {
                const val = Number(e.target.value);
                setQuickSteps(val);
                setMaxSteps(val);
              }}
            >
              <option value={10}>10</option>
              <option value={20}>20</option>
              <option value={30}>30</option>
              <option value={50}>50</option>
            </select>
          </label>

          {busy ? (
            <button
              className="quick-stop-btn"
              onClick={() => void stop()}
              disabled={run?.status === 'stopping'}
            >
              <Square size={13} fill="currentColor" />
              {run?.status === 'stopping' ? 'Stopping…' : 'Stop'}
            </button>
          ) : (
            <button
              className="quick-run-btn"
              onClick={() => void handleQuickRun()}
              disabled={!quickGoal.trim() || submitting || activeAnywhere || !activeUDID}
            >
              {submitting ? (
                <Loader2 size={13} className="spin" />
              ) : (
                <Play size={13} fill="currentColor" />
              )}
              Execute on Device
            </button>
          )}
        </div>

        {/* Live Execution Feedback & Results */}
        {run && (
          <div className={`quick-task-status ${run.status}`}>
            <div className="quick-status-row">
              <span className={`status-pill ${run.status}`}>
                {busy ? <Loader2 size={12} className="spin" /> : null}
                {run.status === 'succeeded' ? <CheckCircle2 size={12} /> : null}
                {run.status === 'failed' || run.status === 'blocked' ? (
                  <AlertCircle size={12} />
                ) : null}
                {statusLabel}
              </span>
              <span className="step-count">
                Steps: {actions.length}/{run.maxSteps}
              </span>
            </div>

            {busy && latest && (
              <div className="quick-current-action">
                <strong>Current:</strong> {actionDetail(latest)}
              </div>
            )}

            {run.outcome && !busy && (
              <div className="quick-outcome">
                <strong>Outcome:</strong> {run.outcome}
              </div>
            )}

            {run.error && (
              <div className="quick-error">
                <strong>Error:</strong> {run.error}
              </div>
            )}
          </div>
        )}

        {error && !run && (
          <div className="quick-task-error">
            <AlertCircle size={13} />
            <span>{error}</span>
          </div>
        )}
      </div>
    </section>
  );
}
