# Repository Instructions — Mobile Jev

## Project Goal

Mobile Jev is an AI-powered Android automation system. Users will enter a task in the frontend, view the Android device stream, and let the backend repeatedly observe the device, obtain a decision from a decision model, validate and execute an action, and continue until the task is completed or the run fails.

## Active Components

- `backend/`: Python, FastAPI, ADB integration, device observation, decision-model integration, and eventually task execution and run management.
- `frontend/`: Next.js user interface for task input, device streaming, decisions, and task progress.
- `ws-scrcpy-runtime/`: Android device streaming runtime.

Treat `delete/` and other explicitly archived or reference-only code as read-only reference material unless a task specifically requires changes there.

## Mandatory Rules

1. Inspect relevant existing code before editing.
2. Reuse existing architecture, models, configuration, and utilities whenever possible.
3. Do not recreate functionality that already exists.
4. Keep backend, frontend, and streaming responsibilities separate.
5. Keep secrets in environment variables. Never commit API keys or real `.env` files.
6. Document required environment variables in `.env.example`.
7. Validate all decision-model responses before they can trigger device actions.
8. Never let the model execute arbitrary shell commands directly.
9. Add or update tests for every meaningful behavior change.
10. Run relevant tests and report actual results. Never claim unexecuted tests passed.
11. Avoid unrelated refactoring, deleting files, or changing public APIs without a clear requirement.
12. Do not implement future phases unless explicitly requested.

## Current Development Priority

Integrate the TypeSafe System One Python SDK into the existing FastAPI backend. Configure the API endpoint, key, and model through environment variables. Add a decision service, validated request/response models, an API endpoint, and mocked unit tests.

Initially, return decisions without executing device actions. Connect the decision endpoint to the frontend after the backend integration works. Implement the autonomous execution loop and persistent run history in later phases.

## Completion Reporting

After every task, report:
- Files created or modified.
- Functionality implemented.
- Tests executed and their actual results.
- Environment setup required.
- Remaining limitations and recommended next step.