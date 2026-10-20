'use client';

import {
  ArrowUpRight,
  ChevronDown,
  Loader2,
  Play,
  RefreshCw,
  Smartphone,
  Square,
  X,
  Zap,
} from 'lucide-react';
import { examples } from './components/studio-types';
import { useStudio } from './components/use-studio';
import { ActivityPanel } from './components/activity-panel';
import { DevicePanel } from './components/device-panel';
import { Telemetry } from './components/telemetry';

export default function Studio() {
  const state = useStudio();
  const {
    device,
    devices,
    selectedDeviceId,
    selectDevice,
    loadingDevices,
    loadDevices,
    loadDevice,
    model,
    goal,
    goalInput,
    busy,
    setGoal,
    maxSteps,
    setMaxSteps,
    stop,
    run,
    submitting,
    clearing,
    start,
    activeAnywhere,
    error,
    setError,
    connected,
    deviceError,
  } = state;

  const activeUDID = selectedDeviceId || device?.id || '';
  const isDeviceConnected = connected && !deviceError;

  return (
    <div className="studio-app">
      {/* 1. Main Header */}
      <header className="main-header">
        <div className="header-left">
          <div className="brand-logo" aria-hidden="true">
            <Smartphone size={20} className="brand-icon" />
          </div>
          <div className="brand-info">
            <h1 className="brand-title">Mobile Jev</h1>
            <span className="brand-tag">Autonomous Agent</span>
          </div>
        </div>

        <div className="header-right">
          <div className="header-device-selector" title="Select connected Android device">
            <Smartphone size={14} className="device-select-icon" />
            <select
              id="header-device-select"
              aria-label="Connected device"
              value={activeUDID}
              disabled={busy || loadingDevices}
              onChange={(e) => selectDevice(e.target.value)}
            >
              {devices.length === 0 ? (
                <option value="">
                  {loadingDevices
                    ? 'Loading devices…'
                    : device?.id
                      ? `${device.name} (${device.id})`
                      : 'No devices connected'}
                </option>
              ) : (
                devices.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.id})
                    {d.state && d.state !== 'ready' && d.state !== 'device' ? ` — ${d.state}` : ''}
                  </option>
                ))
              )}
            </select>
            <ChevronDown size={13} className="device-select-chevron" />
          </div>

          <button
            type="button"
            className="header-refresh-btn"
            onClick={() => {
              void loadDevices();
              void loadDevice();
            }}
            disabled={loadingDevices || busy}
            title="Refresh connected devices"
            aria-label="Refresh connected devices"
          >
            <RefreshCw size={13} className={loadingDevices ? 'spin' : ''} />
          </button>

          <div
            className={`connection-status-pill ${
              isDeviceConnected ? 'connected' : deviceError ? 'error' : 'connecting'
            }`}
            title={
              isDeviceConnected
                ? 'Device stream connected and live'
                : deviceError
                  ? 'Device connection error'
                  : 'Connecting to device stream'
            }
          >
            <span className="status-indicator-dot" />
            <span className="status-indicator-text">
              {isDeviceConnected ? 'Live' : deviceError ? 'Offline' : 'Connecting'}
            </span>
          </div>
        </div>
      </header>

      {/* Main Workspace Layout */}
      <main className="main-workspace">
        <div className="workspace-grid">
          {/* Left Panel — Mission, Quick Start, Metrics, Activity */}
          <section className="left-panel">
            {/* 2. Mission Card */}
            <div className="card mission-card">
              <div className="card-header">
                <div className="header-title-wrap">
                  <h2 className="panel-heading">Mission</h2>
                  <span className="panel-subheading">Ask Jev to perform a task on your device</span>
                </div>
                <span className="model-chip" title="Active decision model">
                  <Zap size={12} fill="currentColor" />
                  {model}
                </span>
              </div>

              <div className="mission-input-wrap">
                <label className="sr-only" htmlFor="goal">
                  Task goal
                </label>
                <textarea
                  id="goal"
                  ref={goalInput}
                  placeholder="Ask Jev to do something on your phone…"
                  value={goal}
                  maxLength={4000}
                  disabled={busy}
                  onChange={(event) => setGoal(event.target.value)}
                  onKeyDown={(event) => {
                    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                      event.preventDefault();
                      void start();
                    }
                  }}
                />
              </div>

              <div className="mission-controls">
                <div className="mission-selectors">
                  <label className="control-select-label">
                    <span>Limit:</span>
                    <div className="select-wrapper">
                      <select
                        aria-label="Step limit"
                        value={maxSteps}
                        disabled={busy}
                        onChange={(event) => setMaxSteps(Number(event.target.value))}
                      >
                        <option value={10}>10 steps</option>
                        <option value={20}>20 steps</option>
                        <option value={30}>30 steps</option>
                        <option value={50}>50 steps</option>
                      </select>
                      <ChevronDown size={12} />
                    </div>
                  </label>

                  <div className="composer-device-wrap" title="Execution target device">
                    <Smartphone size={13} className="composer-device-icon" />
                    <select
                      id="mission-device-select"
                      aria-label="Target device"
                      value={activeUDID}
                      disabled={busy || loadingDevices}
                      onChange={(event) => selectDevice(event.target.value)}
                    >
                      {devices.length === 0 ? (
                        <option value="">
                          {loadingDevices
                            ? 'Loading…'
                            : device?.id
                              ? `${device.name}`
                              : 'No device'}
                        </option>
                      ) : (
                        devices.map((d) => (
                          <option key={d.id} value={d.id}>
                            {d.name || d.id}
                          </option>
                        ))
                      )}
                    </select>
                    <ChevronDown size={12} className="composer-device-chevron" />
                  </div>
                </div>

                <div className="mission-actions">
                  {busy ? (
                    <button
                      type="button"
                      className="stop-task-button"
                      onClick={() => void stop()}
                      disabled={run?.status === 'stopping'}
                    >
                      <Square size={13} fill="currentColor" />
                      {run?.status === 'stopping' ? 'Stopping…' : 'Stop Task'}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="run-task-button"
                      onClick={() => void start()}
                      disabled={
                        !goal.trim() ||
                        submitting ||
                        clearing ||
                        activeAnywhere ||
                        (!selectedDeviceId && !device?.id)
                      }
                    >
                      {submitting ? (
                        <Loader2 size={14} className="spin" />
                      ) : (
                        <Play size={13} fill="currentColor" />
                      )}
                      Run Task
                      <span className="keyboard-shortcut">⌘ ↵</span>
                    </button>
                  )}
                </div>
              </div>

              {error && (
                <div className="inline-error-banner" role="alert">
                  <div className="error-text">{error}</div>
                  <button
                    aria-label="Dismiss error"
                    className="dismiss-error-btn"
                    onClick={() => setError('')}
                  >
                    <X size={14} />
                  </button>
                </div>
              )}
            </div>

            {/* 3. Quick Start */}
            <div className="quick-start-section">
              <span className="section-label">Quick Start</span>
              <div className="quick-start-grid">
                {examples.map((example) => (
                  <button
                    key={example.label}
                    type="button"
                    className="quick-start-btn"
                    disabled={busy}
                    onClick={() => {
                      setGoal(example.goal);
                      goalInput.current?.focus();
                    }}
                  >
                    <span className="quick-start-icon">
                      <example.icon size={14} />
                    </span>
                    <span className="quick-start-label">{example.label}</span>
                    <ArrowUpRight size={13} className="quick-start-arrow" />
                  </button>
                ))}
              </div>
            </div>

            {/* 4. Metrics (Telemetry) */}
            <Telemetry state={state} />

            {/* 5. Activity Panel */}
            <ActivityPanel state={state} />
          </section>

          {/* Right Panel — Live Device & Autonomous Task Execution */}
          <DevicePanel state={state} />
        </div>
      </main>
    </div>
  );
}
