'use client';

import { Clock3, Layers, Zap } from 'lucide-react';
import type { StudioState } from './use-studio';

export function Telemetry({ state }: { state: StudioState }) {
  const { busy, timer, actions, run, modelLatency, maxSteps } = state;
  const totalAllowed = run ? run.maxSteps : maxSteps;

  return (
    <div className="metrics-grid">
      {/* Metric 1: Task Duration */}
      <div className={`metric-card ${busy ? 'is-running' : ''}`}>
        <div className="metric-header">
          <span className="metric-title">
            <Clock3 size={13} className="metric-icon" />
            Task Duration
          </span>
          {busy && <span className="metric-live-badge">Live</span>}
        </div>
        <div className="metric-value-wrap">
          <span className="metric-big-num">{timer.main}</span>
          <span className="metric-fraction">.{timer.fraction}</span>
        </div>
        <span className="metric-subtitle">
          {busy ? 'Running timer' : actions.length > 0 ? 'Total time elapsed' : 'Ready'}
        </span>
      </div>

      {/* Metric 2: Actions Count */}
      <div className="metric-card">
        <div className="metric-header">
          <span className="metric-title">
            <Layers size={13} className="metric-icon" />
            Action Count
          </span>
        </div>
        <div className="metric-value-wrap">
          <span className="metric-big-num">{String(actions.length).padStart(2, '0')}</span>
          <span className="metric-unit">/ {totalAllowed}</span>
        </div>
        <span className="metric-subtitle">
          {actions.length > 0 ? `${actions.length} executed` : 'Awaiting actions'}
        </span>
      </div>

      {/* Metric 3: Model Latency */}
      <div className="metric-card">
        <div className="metric-header">
          <span className="metric-title">
            <Zap size={13} className="metric-icon" />
            Model Latency
          </span>
        </div>
        <div className="metric-value-wrap">
          <span className="metric-big-num">{modelLatency ?? '—'}</span>
          {modelLatency !== null && <span className="metric-unit">ms</span>}
        </div>
        <span className="metric-subtitle">Average decision</span>
      </div>
    </div>
  );
}
