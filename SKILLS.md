# Mobile Jev — Project Knowledge

## 1. Architecture

### Backend
Location: `backend/`

Technology: Python, FastAPI, Pydantic, ADB.

Responsibilities:
- Expose HTTP APIs.
- Communicate with Android through ADB.
- Capture screenshots and retrieve UI hierarchy.
- Parse UI elements and device state.
- Integrate a decision model.
- Eventually validate and execute actions and manage task runs.

Inspect the actual repository before assuming a module, endpoint, or model exists.

### Frontend
Location: `frontend/`

Technology: Next.js and React.

Responsibilities:
- Accept user tasks.
- Display the Android device stream.
- Show model decisions and task progress.
- Eventually provide run controls and run history.

Preserve existing UI conventions and API integration patterns.

### Streaming Runtime
Location: `ws-scrcpy-runtime/`

Responsibility: Android device streaming.

Inspect its existing configuration and startup workflow before changing it. Reuse the existing streaming implementation rather than creating a second streaming system.

### Reference Material
Archived folders, including `delete/`, are not active development targets. Read them only when needed to understand previous implementations or migration decisions.

## 2. Current Backend Foundation

The following functionality has implementation and/or unit-test evidence:
- FastAPI application and API routing.
- ADB device communication.
- Screenshot capture implementation.
- UI hierarchy XML parsing.
- Structured UI elements and bounds.
- Password-value redaction.
- Screen fingerprinting.
- Phone and input-focus state derivation.
- Device error and timeout handling.
- Installed-app caching.

The last reported backend test run had 23 passing tests and one skipped live-device integration test. Re-run tests to establish the current status. Passing unit tests do not prove the complete real-device workflow works.

## 3. Target Automation Workflow

The intended workflow is:

1. Receive a user task.
2. Observe the current Android screen.
3. Build a list of permitted actions.
4. Send the task, observation, and permitted actions to the decision model.
5. Validate the model's response.
6. Execute the selected action through the backend.
7. Observe the resulting screen.
8. Repeat until task completion, cancellation, or a failure limit.
9. Report the final result.

Do not implement this entire workflow in a single change. Build and test each stage separately.

## 4. Decision Model Integration

Current priority: integrate the TypeSafe System One Python SDK.

Configuration must use environment variables for:
- API key.
- API base URL.
- Default model.

Requirements:
- Keep credentials out of source control and logs.
- Use the official SDK API and types.
- Validate structured responses.
- Restrict decisions to known action IDs.
- Handle timeouts, provider errors, and invalid responses.
- Test with mocked responses before using real credentials.
- Initially return decisions without executing actions.

Do not assume the provider's URL, model identifier, or response schema without checking the installed SDK and configuration.

## 5. Development and Testing

Run commands from the repository root unless the command specifies otherwise.

- `make help`: list available commands.
- `make install-backend`: install backend dependencies.
- `make dev-backend`: start the FastAPI server.
- `make test-backend`: run backend tests.
- `make devices`: list Android devices.
- `make clean-ports`: stop processes using the configured backend port, if this target exists in the current Makefile.
- `make export-backend`: export backend source files for review.

For real-device testing:
- Confirm the device appears as `device` in `adb devices -l`.
- Run the existing observation integration test.
- Verify screenshots, XML, structured elements, and device state on the actual device.

Inspect the Makefile before relying on target names or behavior.

## 6. Implementation Workflow

Before changing code:
1. Read the relevant source files and tests.
2. Identify existing abstractions and dependencies.
3. State the smallest implementation plan.
4. Make only the required changes.
5. Add tests.
6. Run the relevant verification commands.
7. Summarize changes and remaining work.

Avoid unrelated refactoring, duplicate implementations, and speculative features.

## 7. Future Phases

After decision-model integration:
1. Connect the decision API to the frontend.
2. Implement validated action execution.
3. Build the observe-decide-execute loop.
4. Add task completion checks, cancellation, and failure limits.
5. Stream task events and screenshots to the frontend.
6. Add run lifecycle management and PostgreSQL persistence.
7. Test the complete workflow on a real Android device.

Do not treat future phases as completed merely because related files exist.