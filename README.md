# Backend File Map + Execution Sequence (with Mermaid Diagrams)

Below is a per-file breakdown of the `backend/` tree from your screenshot, followed by Mermaid diagrams showing the sequence of a full agent run.

---

## 1. What Each File Does

### Top-level files

| File | Role |
|---|---|
| `__init__.py` | Marks `backend/` as a Python package. |
| `config.py` | Defines `Settings` (pydantic-settings) — env vars, `.env` loading, `get_settings()` cached singleton. This is where `TYPESAFE_API_KEY`, `ADB_DEVICE_SERIAL`, `LOG_LEVEL`, limits, etc. live. |
| `main.py` | FastAPI app entry point. Creates the `FastAPI` instance, attaches CORS, exception handlers, mounts routers (twice: with and without `/api/studio` prefix), and defines the `lifespan` (calls `init_client()` on startup, `close_client()` + stop runs on shutdown). |
| `pyproject.toml` | Project metadata + dependencies (`fastapi`, `uvicorn`, `httpx`, `pydantic`, `typesafe-sdk`, `sse-starlette`, …) + pytest config (`asyncio_mode = "auto"`). |
| `backend_contents.txt` | Not code — likely a dump/snapshot of the source tree (ignore for runtime). |
| `.gitignore` | Git ignore rules. |

### `models/` — Pydantic data contracts

| File | Role |
|---|---|
| `__init__.py` | Package marker. |
| `device.py` | `Bounds`, `Element`, `ScreenBounds`, `FocusedElement`, `PhoneState`, `Observation`, `DeviceInfo`, `InstalledApp`. The central `Observation` contract that everything downstream consumes. Includes dict-like `__getitem__`/`.get()` shims. |
| `action.py` | Action variant models (`TapAction`, `TapElementAction`, `SwipeAction`, `TypeAction`, `ClearAction`, `KeyAction`, `GlobalAction`, `OpenAppAction`, `WaitAction`) + `Action` union + `KEYS` / `GLOBAL_KEYCODES` maps. |
| `decision.py` | `ActionOption`, `DecisionRequest`, `DecisionResponse` — the HTTP contract for `POST /decision`. |
| `run.py` | `Event`, `Run`, `RunRequest` — the run state + SSE event contract. |

### `routers/` — HTTP surface (FastAPI `APIRouter`)

| File | Role |
|---|---|
| `__init__.py` | Package marker. |
| `device.py` | `GET /device`, `GET /device/screenshot`, `GET /apps`, `GET /devices`. Injects `ADBDevice` via `_get_device` dependency. |
| `runs.py` | `GET /runs`, `POST /runs`, `POST /runs/clear`, `GET /runs/{id}`, `POST /runs/{id}/stop`, `GET /runs/{id}/events` (SSE). Delegates to `RunStore`. |
| `decision.py` | `POST /decision` — stateless inference. Delegates to `DecisionModelService.decide`. Maps `MissingConfigError→503`, `InvalidResponseError→422`, `DecisionModelError→502`. |
| `health.py` | `GET /health` — checks ADB reachable, device ready, UI observation, TypeSafe key present. |

### `services/` — Business logic (the heart)

| File | Role |
|---|---|
| `__init__.py` | Package marker. |
| `actions.py` | `candidates_for(observation, texts)` — enumerates every possible action (taps, scrolls, back/home, enter, text entries). `describe_action(action, observation)` — human-readable labels. |
| `adb_device.py` | `ADBDevice` — the ADB transport. `_adb`, `_adb_bytes`, `_shell`, `list_devices`, `assert_ready`, `list_apps` (cached, bounded concurrency), `observe` (delegates to `AndroidObserver`), `screenshot`, `act` (dispatches taps/swipes/text/keys + staleness guard), `get_device_info`. Also has `summarize_state` (legacy/dead) and key maps. |
| `agent.py` | `run_agent` / `_run_agent_loop` — the **observe → decide → act** loop. Handles max steps, WAIT backoff, stuck detection, stale retries, input verification, settle polling, and result shaping (`{status, steps, decision, observation, timings}`). |
| `decision_model.py` | `DecisionModelService` — wraps `AsyncTypeSafeClient`. **The only place that calls the model** (`await self._client.system_one(...)`). Two methods: `decide()` (flat, for `/decision`) and `decide_hierarchical()` (multi-question, for the agent). Also `init_client`, `close_client`, `get_service`, `_safe_raise` (API-key scrubbing). |
| `input_verification.py` | `prepare_input_verification` (build receipt for `type`+`clear`), `input_matches` (does the field now contain the text?), `confirm_input` (poll until verified or timeout). |
| `metrics.py` | `summarize_metrics`, `_stats`, `TimedRequest` (mostly a stub; not wired into the main flow). |
| `observer.py` | `AndroidObserver` — parallel capture of `uiautomator dump`, `dumpsys window displays`, `dumpsys input_method`, `wm size`, `screencap`. Parses XML → `Element[]`, derives `PhoneState`, computes SHA-256 fingerprint, returns `Observation`. Defines the `ObservationError` hierarchy. |
| `phone_state.py` | `parse_phone_state(window_dump, ime_dump)` — extracts foreground package, current app, keyboard visibility, editable state. `parse_screen_size(wm_output)` — width/height. |
| `policy.py` | `TypeSafePolicy` — builds the hierarchical question set (`build_questions`), injects `{goal, rules}`, guards 150KB body size, calls `service.decide_hierarchical`, validates via `validate_choice`, resolves the chosen branch to a concrete action dict. Also `RULES` (the instruction text). |
| `run_logger.py` | `RunContext` (correlation ID), `StageContext` (stage name + duration), `CorrelationFormatter/Filter`, `sanitize_value` (redacts keys/bytes/XML), `setup_logging`. Uses `contextvars` so concurrent runs don't mix. |
| `staleness.py` | `assert_fresh(current, expected, action, max_age_ms)` — raises `StaleObservationError` if device/package/screen/age/target meaning diverged. `_target_meaning`, `_navigation_meaning` helpers. |
| `store.py` | `RunStore` — in-memory runs dict, pub/sub listeners, `asyncio.Task` per run, `start`, `stop`, `clear`, `get`, `list`, `subscribe`, `_run_agent_task`. `StudioError` (HTTP-coded app error). `get_store()` singleton. |
| `text.py` | `text_candidates(goal, supplied)` — extracts 1..8-word substrings from the goal (punctuation-stripped, deduped, overflow at 254) or returns supplied texts. |
| `ui_parser.py` | Legacy/alternate XML → a11y dict parser (`parse_ui_xml`). Only referenced by `tests/test_api.py`; production uses `observer.parse_ui_hierarchy`. |
| `verifiers.py` | `dark_theme_state`, `verify_task_completion` — heuristic completion checks (app launch, dark theme, keyword visibility). Not wired into `agent.py`; used by tests. |

### `tests/`

| File | Role |
|---|---|
| `__init__.py` | Package marker. |
| `test_api.py` | Smoke tests: app boots, models instantiate, `ui_parser` works, `/device` and `/devices` return 200 offline, `RunRequest` aliases. |
| `test_run_logger.py` | Correlation ID propagation, concurrent isolation, stage nesting, formatter output, sanitization, file/console logging, `RunStore` correlation ID. |
| `test_observer_integration.py` | Live-device observation test (skipped unless `LIVE_DEVICE_TEST=1` and `adb get-state == device`). |
| `test_agent_policy.py` | `build_questions`, `validate_choice`, `TypeSafePolicy.decide` (hierarchical), `verify_task_completion`, `run_agent` loop with mocked device/policy, config sync. |
| `test_observer.py` | XML parsing, password redaction, bounds clamping, fingerprint determinism/sensitivity, `derive_phone_state`, full pipeline with `MockADBExecutor`, error cases. |
| `test_apps_cache.py` | `ADBDevice.list_apps` cache hit/force refresh/device isolation/label fallback/concurrency/error isolation/benchmark. |
| `test_decision.py` | `DecisionModelService.decide` (valid, done via key, done via noul, invalid, timeout, connection error, key scrubbing, empty inputs), lifecycle, `/decision` endpoint (200/503/422/502/validation). |
| `test_staleness.py` | `assert_fresh` with Pydantic and dict inputs, detects changed package/screen/element/expired. |

---

## 2. Layered Architecture

```mermaid
graph TD
    subgraph Client
        UI[Next.js UI / curl]
    end

    subgraph "Entry"
        MAIN[main.py<br/>FastAPI + lifespan]
        CFG[config.py<br/>Settings]
    end

    subgraph "Routers (HTTP)"
        R_DEV[routers/device.py]
        R_RUN[routers/runs.py]
        R_DEC[routers/decision.py]
        R_HEA[routers/health.py]
    end

    subgraph "Models (contracts)"
        M_DEV[models/device.py]
        M_ACT[models/action.py]
        M_DEC[models/decision.py]
        M_RUN[models/run.py]
    end

    subgraph "Services (logic)"
        S_STORE[services/store.py<br/>RunStore]
        S_AGENT[services/agent.py<br/>run_agent]
        S_POL[services/policy.py<br/>TypeSafePolicy]
        S_DM[services/decision_model.py<br/>DecisionModelService]
        S_ACT[services/actions.py]
        S_TXT[services/text.py]
        S_ADB[services/adb_device.py<br/>ADBDevice]
        S_OBS[services/observer.py<br/>AndroidObserver]
        S_PS[services/phone_state.py]
        S_STALE[services/staleness.py]
        S_IV[services/input_verification.py]
        S_LOG[services/run_logger.py]
        S_VER[services/verifiers.py]
        S_UP[services/ui_parser.py]
        S_MET[services/metrics.py]
    end

    subgraph "External"
        ADB[(Android device<br/>via adb)]
        TS[(TypeSafe System One<br/>cloud API)]
    end

    UI -->|HTTP| MAIN
    MAIN --> CFG
    MAIN --> R_DEV & R_RUN & R_DEC & R_HEA

    R_DEV --> S_ADB
    R_RUN --> S_STORE
    R_DEC --> S_DM
    R_HEA --> S_ADB

    S_STORE --> S_AGENT
    S_AGENT --> S_POL & S_ADB & S_ACT & S_IV & S_STALE & S_LOG
    S_POL --> S_DM & S_ACT & S_TXT & S_LOG
    S_DM --> TS
    S_ADB --> S_OBS
    S_OBS --> S_PS & S_LOG
    S_OBS --> M_DEV
    S_AGENT --> M_DEV
    R_RUN --> M_RUN
    R_DEC --> M_DEC
    S_POL --> M_ACT

    S_ADB -->|adb shell| ADB
    S_OBS -->|adb shell / exec-out| ADB
```

---

## 3. Request Routing (who handles what)

```mermaid
graph LR
    C[Client] -->|GET /device| RD[routers/device.py]
    C -->|GET /device/screenshot| RD
    C -->|GET /apps| RD
    C -->|GET /devices| RD
    C -->|GET /health| RH[routers/health.py]
    C -->|POST /decision| RDec[routers/decision.py]
    C -->|GET /runs| RR[routers/runs.py]
    C -->|POST /runs| RR
    C -->|POST /runs/clear| RR
    C -->|GET /runs/:id| RR
    C -->|POST /runs/:id/stop| RR
    C -->|GET /runs/:id/events SSE| RR

    RD --> D[ADBDevice]
    RH --> D
    RDec --> DM[DecisionModelService]
    DM --> TS[(TypeSafe)]
    RR --> RS[RunStore]
    RS --> AG[run_agent]
    AG --> POL[TypeSafePolicy]
    POL --> DM
    AG --> D
    D --> OBS[AndroidObserver]
    OBS --> AND[(Android)]
```

---

## 4. Startup Sequence

```mermaid
sequenceDiagram
    autonumber
    participant U as uvicorn
    participant M as main.py
    participant C as config.py
    participant L as run_logger
    participant DM as decision_model
    participant APP as FastAPI app

    U->>M: import app
    M->>L: setup_logging() (eager)
    M->>APP: FastAPI(lifespan=...)
    Note over APP: startup begins
    APP->>C: get_settings()
    C-->>APP: Settings
    APP->>L: setup_logging(level, file, console)
    APP->>DM: init_client()
    alt TYPESAFE_API_KEY present
        DM->>DM: AsyncTypeSafeClient(...)
        DM-->>APP: service ready
    else missing
        DM-->>APP: MissingConfigError → warn only
    end
    APP-->>U: app serving
    Note over APP: shutdown
    APP->>DM: close_client()
    APP->>APP: stop all running runs
```

---

## 5. `/decision` — Stateless Inference

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant R as routers/decision.py
    participant DM as DecisionModelService
    participant TS as TypeSafe API

    C->>R: POST /decision {task, observation, actions}
    R->>DM: decide(task, observation, actions)
    DM->>DM: build criteria + {action:Choice, task_complete:Noul}
    DM->>TS: await client.system_one(state, questions)
    TS-->>DM: response (choices, nouls, model)
    alt chosen == "__done__" or noul >= 0.9
        DM-->>R: DecisionResponse(done=True, selected_action_id=None)
    else chosen in action_ids
        DM-->>R: DecisionResponse(done=False, selected_action_id=chosen)
    else invalid
        DM-->>R: InvalidResponseError
        R-->>C: 422
    end
    R-->>C: 200 JSON DecisionResponse
```

---

## 6. `/runs` — Autonomous Agent (the main flow)

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant RR as routers/runs.py
    participant RS as RunStore
    participant AG as run_agent
    participant POL as TypeSafePolicy
    participant DM as DecisionModelService
    participant D as ADBDevice
    participant OBS as AndroidObserver
    participant ADB as Android
    participant TS as TypeSafe

    C->>RR: POST /runs {goal, maxSteps, deviceId}
    RR->>RS: start(goal, max_steps, device_id, cid)
    RS->>RS: validate, create run dict, status="running"
    RS->>AG: asyncio.create_task(_run_agent_task)
    RS-->>RR: snapshot
    RR-->>C: 201 {run}

    C->>RR: GET /runs/{id}/events (SSE)
    RR->>RS: subscribe(run_id, on_update)
    RS-->>RR: (listener registered)
    RR-->>C: yield initial snapshot

    Note over RS,AG: _run_agent_task running
    RS->>AG: run_agent(device, policy, goal, ...)
    AG->>D: assert_ready()
    D->>ADB: adb get-state
    AG->>D: observe() + list_apps() (parallel)
    D->>OBS: AndroidObserver.observe()
    OBS->>ADB: uiautomator dump / dumpsys window / dumpsys ime / wm size / screencap
    OBS-->>D: Observation
    D-->>AG: observation dict

    loop for attempt in range(max_steps*2+4)
        AG->>POL: decide(goal, observation, history, texts, apps)
        POL->>POL: build_questions + inject {goal, rules} + size guard
        POL->>DM: decide_hierarchical(model, state, questions)
        DM->>TS: await client.system_one(state, questions)
        TS-->>DM: answers
        DM-->>POL: {answers, usage, model}
        POL->>POL: validate_choice(operation) + resolve target
        POL-->>AG: {status, operation, action, confidence, ...}
        AG->>RS: on_step(...) → publish → SSE
        alt status != "action" / not execute / step limit
            AG-->>RS: finish(...)
        else action
            AG->>D: act(action, expected=observation)
            D->>OBS: observe() (fresh)
            D->>D: assert_fresh(current, expected, action)
            D->>ADB: input tap / swipe / text / keyevent / monkey
            D-->>AG: receipt (inputVerification?)
            AG->>RS: on_action(...) → publish → SSE
            opt inputVerification
                AG->>AG: confirm_input(poll observe until text matches)
            end
            AG->>AG: settle poll (observe until fingerprint changes)
            AG->>RS: on_observation(...) → SSE
        end
    end
    AG-->>RS: {status, steps, decision, observation, timings}
    RS->>RS: emit result event, _finish(status)
    RS-->>RR: publish final snapshot
    RR-->>C: SSE final state, stream closes
```

---

## 7. Inside One Agent Loop Iteration

```mermaid
flowchart TD
    A([Loop iteration]) --> B["policy.decide(goal, observation, history, texts, apps)"]
    B --> B1[text_candidates from goal]
    B --> B2[build_questions → candidates_for]
    B --> B3[inject goal/rules into questions]
    B --> B4["size guard: len(json(body)) > 150_000?"]
    B4 -- yes --> BX([ValueError → run fails])
    B4 -- no --> B5["DecisionModelService.decide_hierarchical"]
    B5 --> B6["AsyncTypeSafeClient.system_one()"]
    B6 --> B7["validate_choice(operation)"]
    B7 --> B8["resolve target branch (tap/scroll/text/app/control)"]
    B8 --> C{decision.status}
    C -- "done/blocked/needs_input/uncertain" --> Z([finish with status])
    C -- "action" --> D{execute?}
    D -- no --> P([finish preview])
    D -- yes --> E{step >= max_steps?}
    E -- yes --> SL([finish step_limit])
    E -- no --> F["signature = fingerprint + action; repeated?"]
    F -- yes, not wait --> ST([finish stuck])
    F -- no --> G{is_wait?}
    G -- yes --> W["budget = min(backoff, remaining)"]
    W --> W2["wait(budget); if total >= wait_timeout_ms → loading_timeout"]
    G -- no --> H["device.act(action, expected=observation)"]
    H --> H1[observe fresh]
    H1 --> H2{assert_fresh ok?}
    H2 -- no --> S1["StaleObservationError → staleRetries++"]
    S1 --> S2{staleRetries >= 3?}
    S2 -- yes --> US([finish unstable_screen])
    S2 -- no --> S3[re-observe, continue]
    H2 -- yes --> H3[dispatch: input tap/swipe/text/keyevent/monkey]
    H3 --> H4[receipt inputVerification?]
    H4 -- yes --> H5["confirm_input(poll) → verified?"]
    H5 -- no --> IU([finish input_unverified])
    H5 -- yes --> I
    H4 -- no --> I[settle loop: observe until fingerprint changes or timeout]
    I --> J[append history entry]
    J --> K[observation = after; next iteration]
    K --> A
```

---

## 8. Data Contracts at Each Hop

```mermaid
graph LR
    subgraph Observation
        OBS["Observation{deviceId, phone, screen, elements, fingerprint, observedAt}"]
    end
    subgraph Questions
        Q["questions{operation:Choice, tap_target?, scroll_target?, text_value?, app_target?}"]
        ST["state{goal, app, isEditable, textSource, focusedField, visibleText, elements, availableApps, recentActions}"]
    end
    subgraph Decision
        DEC["decision{status, operation, target, choice, confidence, action?, label?}"]
    end
    subgraph Run
        RUN["Run{id, goal, status, events[], outcome, correlationId}"]
        EV["Event{type:decision|action|result|error, step, operation, label, ...}"]
    end
    OBS --> Q
    Q --> DEC
    DEC --> RUN
    RUN --> EV
```

---

## 9. File → Responsibility Cheat Sheet

| Layer | Files | Responsibility |
|---|---|---|
| **Entry** | `main.py`, `config.py` | App wiring, lifespan, env settings |
| **HTTP** | `routers/*.py` | Validate requests, call services, shape responses, map errors to status codes |
| **Contracts** | `models/*.py` | Pydantic schemas with aliases + dict-like shims |
| **Orchestration** | `services/store.py`, `services/agent.py` | Run lifecycle, task spawning, observe→decide→act loop |
| **Decision** | `services/policy.py`, `services/actions.py`, `services/text.py` | Build question set, enumerate candidates, extract text, validate choice |
| **Model transport** | `services/decision_model.py` | **Only** place that calls `AsyncTypeSafeClient.system_one` |
| **Device** | `services/adb_device.py` | ADB commands, action dispatch, app cache, screenshot |
| **Perception** | `services/observer.py`, `services/phone_state.py`, `services/ui_parser.py` (legacy) | XML parse, fingerprint, phone state, screen size |
| **Safety** | `services/staleness.py`, `services/input_verification.py` | Freshness guard, typed-text confirmation |
| **Observability** | `services/run_logger.py` | Correlation IDs, stage timings, sanitization, formatter |
| **Heuristics** | `services/verifiers.py`, `services/metrics.py` | Completion checks, metric aggregation (stubs / not on main path) |
| **Tests** | `tests/*.py` | Unit + integration + live-device coverage |

---

## 10. TL;DR Sequence

```
Startup:   main.py → config.get_settings() → init_client() → serve
/decision: routers/decision.py → DecisionModelService.decide → system_one → DecisionResponse
/runs:     routers/runs.py → RunStore.start → asyncio task
             └─ run_agent
                  ├─ assert_ready + observe (AndroidObserver) + list_apps
                  └─ loop:
                       policy.decide → build_questions → decide_hierarchical → system_one
                                     → validate_choice → resolve action
                       device.act   → observe (fresh) → assert_fresh → adb input …
                                     → receipt → confirm_input (poll)
                       settle       → observe until fingerprint changes
                       emit events  → RunStore._publish → SSE
                  finish(status) → RunStore._finish → SSE final
```

Everything is layered so that **only `services/decision_model.py` talks to the model**, **only `services/adb_device.py` talks to ADB**, and **`services/agent.py` orchestrates without knowing either detail** — it just calls `policy.decide` and `device.act`.