'use client';

import {
  Activity,
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCircle2,
  Clock3,
  CornerDownLeft,
  History,
  Loader2,
  MousePointer2,
  Play,
  RotateCcw,
  Sparkles,
  XCircle,
} from 'lucide-react';
import { actionDetail, operationName, statusLabels } from './studio-types';
import type { StudioState } from './use-studio';

export function ActivityPanel({ state }: { state: StudioState }) {
  const {
    panel,
    setPanel,
    actions,
    run,
    busy,
    feed,
    decisions,
    eventConnected,
    runs,
    setRun,
    setGoal,
    clear,
    clearing,
    submitting,
    activeAnywhere,
  } = state;

  return (
    <div className="card activity-panel-card">
      <div className="activity-panel-header">
        <div className="activity-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={panel === 'activity'}
            onClick={() => setPanel('activity')}
            className={`tab-btn ${panel === 'activity' ? 'active' : ''}`}
          >
            <Activity size={15} />
            Live Activity
            {actions.length > 0 && <span className="tab-count-badge">{actions.length}</span>}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={panel === 'history'}
            onClick={() => setPanel('history')}
            className={`tab-btn ${panel === 'history' ? 'active' : ''}`}
          >
            <History size={15} />
            Recent Runs
            {runs.length > 0 && <span className="tab-count-badge">{runs.length}</span>}
          </button>
        </div>

        <div className="activity-header-controls">
          <span className={`run-status-pill ${run?.status || 'idle'}`}>
            <span className="status-dot" />
            {run ? statusLabels[run.status] || run.status : 'Standby'}
          </span>
          <button
            type="button"
            className="clear-runs-btn"
            onClick={() => void clear()}
            disabled={activeAnywhere || submitting || clearing || (!runs.length && !run)}
            title={
              activeAnywhere
                ? 'Stop the task before clearing'
                : 'Clear recent runs and reset timeline'
            }
          >
            <RotateCcw size={12} className={clearing ? 'spin' : undefined} />
            {clearing ? 'Clearing…' : 'Clear'}
          </button>
        </div>
      </div>

      {panel === 'activity' ? (
        <div className="activity-feed-body" ref={feed} aria-live="polite">
          {!run ? (
            <div className="activity-empty-state">
              <div className="empty-icon-wrap">
                <Sparkles size={24} className="empty-sparkle-icon" />
              </div>
              <h3 className="empty-title">Ready to execute tasks</h3>
              <p className="empty-desc">
                Your agent&apos;s live decisions, observations, and actions will appear here in
                real-time.
              </p>
              <div className="pipeline-flow-steps">
                <span className="flow-step">Observe</span>
                <ArrowRight size={11} className="flow-arrow" />
                <span className="flow-step">Plan</span>
                <ArrowRight size={11} className="flow-arrow" />
                <span className="flow-step">Decide</span>
                <ArrowRight size={11} className="flow-arrow" />
                <span className="flow-step">Act</span>
              </div>
            </div>
          ) : (
            <div className="timeline-container">
              {/* Task started marker */}
              <div className="timeline-event start-event">
                <div className="timeline-marker start-marker">
                  <Play size={10} fill="currentColor" />
                </div>
                <div className="timeline-event-content">
                  <span className="event-title">Task started</span>
                  <span className="event-timestamp">
                    {new Date(run.startedAt).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                      second: '2-digit',
                    })}
                  </span>
                </div>
              </div>

              {/* Action items in timeline */}
              {actions.map((action, index) => {
                const decision = decisions.findLast(
                  (entry) => entry.step === action.step && entry.at <= action.at,
                );
                const Icon =
                  action.operation === 'WAIT'
                    ? Clock3
                    : action.operation?.startsWith('SCROLL')
                      ? ArrowDown
                      : action.operation === 'TYPE_TEXT'
                        ? CornerDownLeft
                        : MousePointer2;

                return (
                  <div className="timeline-action-card" key={action.sequence}>
                    <div className="timeline-marker action-marker">
                      <span className="step-num">{String(index + 1).padStart(2, '0')}</span>
                    </div>
                    <div className="action-card-inner">
                      <div className="action-card-main">
                        <div className="action-type-badge">
                          <Icon size={13} />
                          <span>{operationName(action.operation)}</span>
                        </div>
                        <p className="action-description">{actionDetail(action)}</p>
                      </div>

                      <div className="action-card-meta">
                        {decision?.latencyMs ? (
                          <span className="decision-latency-chip">
                            {Math.round(decision.latencyMs)} ms
                          </span>
                        ) : null}
                        <span className="executed-status-chip">
                          <Check size={11} />
                          Executed
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}

              {/* Live Thinking / Observing indicator */}
              {busy && (
                <div className="timeline-thinking-card">
                  <div className="timeline-marker thinking-marker">
                    <Loader2 size={13} className="spin" />
                  </div>
                  <div className="thinking-card-content">
                    <span className="thinking-text">
                      {run.status === 'stopping'
                        ? 'Finishing the current action…'
                        : !eventConnected
                          ? 'Reconnecting to live activity…'
                          : 'Jev is observing device screen and planning next action…'}
                    </span>
                    <span className="thinking-pulsing-dots">···</span>
                  </div>
                </div>
              )}

              {/* Completion / Outcome Banner */}
              {!busy && (
                <div className={`timeline-result-card ${run.status}`}>
                  <div className="result-icon-wrap">
                    {run.status === 'succeeded' ? (
                      <CheckCircle2 size={18} className="result-success-icon" />
                    ) : (
                      <XCircle size={18} className="result-failed-icon" />
                    )}
                  </div>
                  <div className="result-details">
                    <strong className="result-heading">
                      {statusLabels[run.status] || run.status}
                    </strong>
                    <p className="result-message">
                      {run.error ||
                        run.events.findLast((e) => e.type === 'result')?.reason ||
                        (run.status === 'succeeded'
                          ? 'Goal completed successfully. Device is ready for next mission.'
                          : run.status === 'stopped'
                            ? 'The task was stopped by user.'
                            : `Agent stopped: ${(run.outcome || 'unknown').replaceAll('_', ' ')}.`)}
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="history-feed-body">
          {runs.length ? (
            <div className="history-items-list">
              {runs.map((item) => (
                <button
                  type="button"
                  className={`history-card-button ${item.id === run?.id ? 'active' : ''}`}
                  key={item.id}
                  disabled={busy && item.id !== run?.id}
                  onClick={() => {
                    setRun(item);
                    setGoal(item.goal);
                    setPanel('activity');
                  }}
                >
                  <div className="history-card-header">
                    <span className={`history-status-indicator ${item.status}`} />
                    <strong className="history-goal-text">{item.goal}</strong>
                    <ArrowUpRight size={14} className="history-open-arrow" />
                  </div>
                  <div className="history-card-footer">
                    <span className="history-status-label">
                      {statusLabels[item.status] || item.status}
                    </span>
                    <span className="history-meta-info">
                      {new Date(item.startedAt).toLocaleTimeString([], {
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                      {item.endedAt
                        ? ` · ${((item.endedAt - item.startedAt) / 1000).toFixed(1)}s`
                        : ''}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="activity-empty-state">
              <div className="empty-icon-wrap">
                <History size={24} className="empty-sparkle-icon" />
              </div>
              <h3 className="empty-title">No recent runs</h3>
              <p className="empty-desc">Completed and saved task runs will be archived here.</p>
            </div>
          )}
        </div>
      )}

      <div className="activity-panel-footer">
        <span className="footer-status-label">
          <span className={`footer-pulse-dot ${busy ? 'pulse' : ''}`} />
          {busy ? 'Streaming agent decisions & actions' : 'Mobile Jev Autonomous Engine'}
        </span>
        <span className="footer-session-code">
          {run ? `RUN #${run.id.slice(0, 8)}` : 'SESSION READY'}
        </span>
      </div>
    </div>
  );
}
