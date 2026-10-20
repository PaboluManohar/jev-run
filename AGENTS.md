# Repository Instructions — Mobile Jev

## Goal

Mobile Jev is an AI-powered Android automation system. Users submit tasks through the Next.js frontend, view a live Android device stream, and let the FastAPI backend autonomously observe the device, obtain decisions from the TypeSafe System One decision model, validate and execute supported actions, and repeat until the task completes or fails.

## Project Structure

- `backend/`: FastAPI, Python, ADB integration, device observation, decision-model integration, action execution, and run management.
- `frontend/`: Next.js UI for task input, live device streaming, execution progress, and results.
- `ws-scrcpy-runtime/`: Android device streaming runtime.
- `delete/` and other archived/reference directories: Read-only unless explicitly required.

## Mandatory Rules

1. Inspect existing code, architecture, configuration, and tests before making changes.
2. Reuse existing components, models, utilities, and conventions. Avoid duplicate implementations and unnecessary refactoring.
3. Keep backend, frontend, and streaming responsibilities separate.
4. Configure the TypeSafe System One SDK using environment variables for the API endpoint, API key, and model. Document required variables in `.env.example`; never commit real secrets.
5. Validate all model inputs and outputs before using decisions. Reject malformed or unsupported actions.
6. Never allow the model to execute arbitrary shell commands. Execute only explicitly supported, validated device actions.
7. Keep APIs and existing functionality backward-compatible unless a change is explicitly required.
8. Add or update tests for meaningful behavior changes. Mock external SDK and model calls in unit tests where appropriate.
9. Run relevant tests, linting, type checks, and builds. Report actual results without claiming unexecuted checks passed.
10. Avoid modifying archived code, deleting files, or changing unrelated functionality.
11. Implement only what is requested. Do not introduce unnecessary features or abstractions.

## Completion Report

After every task, report:
- Files created or modified.
- Functionality implemented.
- Tests and verification commands executed, with actual results.
- Required environment variables or setup changes.
- Remaining limitations and the recommended next step.