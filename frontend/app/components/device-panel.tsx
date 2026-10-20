'use client';

import {
  Camera,
  Expand,
  Radio,
  RefreshCw,
  Smartphone,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { buildStreamUrl } from './studio-types';
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
      <div className="stream-empty-placeholder">
        <Smartphone size={28} className="placeholder-phone-icon" />
        <span>{error ? 'Waiting for device…' : 'Connecting screenshot snapshot…'}</span>
      </div>
    );
  }

  return (
    <img
      src={src}
      alt="Device screen snapshot"
      className="screen-snapshot-img"
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
  isSnapshotMode,
}: {
  udid: string;
  busy: boolean;
  onConnected: (connected: boolean) => void;
  isSnapshotMode: boolean;
}) {
  const [iframeError, setIframeError] = useState(false);

  const streamUrl = useMemo(() => {
    if (!udid) return '';
    return buildStreamUrl(udid);
  }, [udid]);

  if (isSnapshotMode || iframeError) {
    return (
      <div className="stream-viewport-wrapper">
        <LocalDeviceScreen udid={udid} busy={busy} onConnected={onConnected} />
      </div>
    );
  }

  return (
    <div className="stream-viewport-wrapper">
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
      />
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
  } = state;

  const [localConnected, setLocalConnected] = useState(false);
  const [isSnapshotMode, setIsSnapshotMode] = useState(false);
  const [stageSize, setStageSize] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });

  const handleConnected = useCallback(
    (connected: boolean) => {
      setLocalConnected(connected);
      setConnected(connected);
    },
    [setConnected],
  );

  const activeUDID = selectedDeviceId || device?.id || '';
  const activeDevice = devices.find((d) => d.id === activeUDID) || device;

  // Track the actual container dimensions to dynamically fit the phone aspect ratio
  useEffect(() => {
    if (!phoneStage.current) return;
    const node = phoneStage.current;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          setStageSize((prev) => {
            if (Math.abs(prev.width - width) < 3 && Math.abs(prev.height - height) < 3) {
              return prev;
            }
            return { width, height };
          });
        }
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [phoneStage]);

  // Actual phone screen aspect ratio (OnePlus 9R / standard modern Android = 1080/2400 = 0.45)
  const screenRatio =
    activeDevice?.screenWidth && activeDevice?.screenHeight
      ? activeDevice.screenWidth / activeDevice.screenHeight
      : 1080 / 2400;

  // ws-scrcpy control toolbar width is 52px; snapshot mode has no toolbar
  const toolbarWidth = isSnapshotMode ? 0 : 52;
  const borderWidth = 2; // matches .phone-frame-wrapper border: 2px solid var(--border)

  // Available height inside the container card (decreased height to eliminate top/bottom gaps and provide comfortable padding)
  const availHeight = stageSize.height > 120 ? Math.floor(stageSize.height - 80) : 600;
  const availWidth = stageSize.width > 100 ? stageSize.width : 400;

  // Calculate inner content dimensions so video aspect ratio matches screenRatio exactly with 0 letterboxing
  let innerHeight = Math.max(100, Math.floor(availHeight - 2 * borderWidth));
  let videoWidth = Math.floor(innerHeight * screenRatio);
  innerHeight = Math.floor(videoWidth / screenRatio);
  let innerWidth = videoWidth + toolbarWidth;

  if (innerWidth + 2 * borderWidth > availWidth) {
    innerWidth = Math.max(100, Math.floor(availWidth - 2 * borderWidth));
    videoWidth = Math.max(80, innerWidth - toolbarWidth);
    innerHeight = Math.floor(videoWidth / screenRatio);
  }

  const calculatedWidth = innerWidth + 2 * borderWidth;
  const calculatedHeight = innerHeight + 2 * borderWidth;

  return (
    <section className="right-panel">
      {/* Live Device Container */}
      <div className="card device-container-card">
        <div className="device-container-header">
          <div className="device-header-info">
            <h2 className="panel-heading">Live Device</h2>
            <span className="device-udid-tag" title="Selected Device UDID">
              <Smartphone size={13} />
              {activeDevice?.name || 'Android Device'} {activeUDID ? `(${activeUDID})` : ''}
            </span>
          </div>

          <div className="device-header-actions">
            <span
              className={`device-live-badge ${
                localConnected ? 'is-live' : deviceError ? 'is-offline' : 'is-connecting'
              }`}
            >
              <span className="live-dot" />
              {localConnected ? 'LIVE' : deviceError ? 'OFFLINE' : 'CONNECTING'}
            </span>

            <button
              type="button"
              className={`device-mode-toggle-btn ${isSnapshotMode ? 'active' : ''}`}
              title={isSnapshotMode ? 'Switch to live stream' : 'Switch to snapshot mode'}
              onClick={() => setIsSnapshotMode(!isSnapshotMode)}
            >
              {isSnapshotMode ? (
                <>
                  <Radio size={12} />
                  <span>Live Stream</span>
                </>
              ) : (
                <>
                  <Camera size={12} />
                  <span>Snapshot</span>
                </>
              )}
            </button>

            <button
              type="button"
              className="device-action-icon-btn"
              title="Expand device view"
              aria-label="Expand device view"
              onClick={() => {
                if (document.fullscreenElement) void document.exitFullscreen();
                else void phoneStage.current?.requestFullscreen();
              }}
            >
              <Expand size={14} />
            </button>
          </div>
        </div>

        {/* Device Stage with Stream */}
        <div className="phone-screen-container" ref={phoneStage}>
          <div
            className="phone-frame-wrapper"
            style={{
              width: `${calculatedWidth}px`,
              height: `${calculatedHeight}px`,
            }}
          >
            <div className="phone-screen-viewport">
              {deviceError ? (
                <div className="stream-error-view">
                  <Smartphone size={36} className="error-phone-icon" />
                  <h3 className="error-heading">Connection interrupted</h3>
                  <p className="error-desc">{deviceError}</p>
                  <button
                    type="button"
                    className="reconnect-btn"
                    onClick={() => {
                      void loadDevices();
                      void loadDevice();
                    }}
                  >
                    <RefreshCw size={13} />
                    Reconnect Device
                  </button>
                </div>
              ) : activeUDID ? (
                <LiveDeviceStream
                  udid={activeUDID}
                  busy={busy}
                  onConnected={handleConnected}
                  isSnapshotMode={isSnapshotMode}
                />
              ) : (
                <LocalDeviceScreen udid={activeUDID} busy={busy} onConnected={handleConnected} />
              )}
            </div>
          </div>

          <div
            className="stream-footer-caption"
            style={{
              width: `${calculatedWidth}px`,
            }}
          >
            <span className="caption-status">
              <span className={`caption-dot ${busy ? 'busy' : ''}`} />
              {busy
                ? 'Agent in control'
                : localConnected
                  ? 'ws-scrcpy live stream active'
                  : 'Connecting stream…'}
            </span>
            <span className="caption-udid">
              {activeUDID ? `UDID: ${activeUDID}` : 'ANDROID · LOCAL ADB'}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
