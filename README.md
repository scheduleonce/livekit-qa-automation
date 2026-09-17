# LiveKit QA Automation Framework

This repository contains a voice automation framework for validating AI phone agents using LiveKit. It is designed to make AI phone agent testing repeatable, configurable, and accessible to non-technical stakeholders.

## What it does

- Executes real conversation flows with an AI phone agent using `livekit`.
- Uses YAML scenario definitions for test content, expected outcomes, and environment-specific bot selection.
- Resolves credentials and bot IDs from centralized JSON/YAML config files.
- Generates HTML and JUnit-style reports for easy review.
- Supports multiple environments (`App3`, `App2`, etc.) with environment-driven execution.

## Key benefits

- **No-code scenario updates:** Test cases are defined in YAML files, not Python code.
- **Environment-driven execution:** `APP_ENV` or `TEST_ENV` selects the target environment and bot config.
- **Reusable configuration:** Centralized credentials in `data/livekit_config.json` and bot mapping in YAML.
- **Automated validation:** The framework evaluates agent responses against expected outcomes.
- **Clear reporting:** Test artifacts are saved to `reports/` for team review.

## Repository structure

```
pytest.ini
requirements.txt
README.md
data/
  livekit_config.json
  happy_paths.yaml
replyAudioFiles/
reports/
src/
  __init__.py
  config.py
  evaluator.py
  livekit_runner.py
  simulated_user.py
  tts.py
  transcript_exporter.py
  utils.py
tests/
  __init__.py
  conftest.py
  dummy_test_scenarios.py
  test_agent.py
transcripts/
```

## Prerequisites

- Python 3.8+ (recommended)
- `pip`
- Network access to LiveKit and the AI phone agent under test
- Optional: audio playback support if the framework uses local text-to-speech playback

## Install dependencies

```bash
python -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
```

## Configuration

### 1. Set the target environment

The framework checks environment variables in this order:

1. `APP_ENV`
2. `TEST_ENV`
3. `ENV` (legacy compatibility)
4. `App3` (local-development default)

Examples:

```bash
export APP_ENV=App3
```

```powershell
$env:APP_ENV = 'App3'
```

For local development, copy `.env.example` to `.env`. The loader reads `.env` without overriding values already supplied by the shell or Jenkins.

### 2. Configure LiveKit credentials and bot id

Credential resolution is ordered as follows:

1. `LIVEKIT_URL`, `API_KEY`, and `API_SECRET` environment variables.
2. The matching environment in `data/livekit_bot_config.json` for local fallback.

If any environment credential is set, all three must be set; partial configuration fails fast. `BOT_ID` may be supplied as an environment variable, otherwise the scenario YAML `target_bot_id` is mapped through the JSON `BOT_ID` list.

The JSON file contains environment-specific credential and bot mappings for local fallback. Do not commit real credentials. The existing credentials in any previously committed copy should be rotated and replaced with local `.env` values or Jenkins credentials.

Example:

```json
{
  "App3": {
    "LIVEKIT_URL": "wss://app3-livekit.com",
    "API_KEY": "Test@123",
    "API_SECRET": "xxxx",
    "BOT_ID": [
      { "id": "1", "value": "AI3333" },
      { "id": "2", "value": "AI4444" }
    ]
  },
  "App2": {
    "LIVEKIT_URL": "wss://app2-livekit.com",
    "API_KEY": "Test@123",
    "API_SECRET": "xxxx",
    "BOT_ID": [
      { "id": "1", "value": "AI5555" },
      { "id": "2", "value": "AI6666" }
    ]
  }
}
```

### 3. Configure environment-specific bot selection

The YAML file(s) under `data/` define which `target_bot_id` to use for each environment.

Example in `data/happy_paths.yaml`:

```yaml
environments:
  App3:
    target_bot_id: "1"
  App2:
    target_bot_id: "2"
```

## How the framework resolves configuration

1. `APP_ENV`, `TEST_ENV`, legacy `ENV`, or the `App3` local default is selected.
2. Jenkins/shell credentials are read first; otherwise `.env`/`data/livekit_bot_config.json` provide local fallback values.
3. The active YAML scenario file is parsed for `environments -> <ENV> -> target_bot_id`.
4. The JSON `BOT_ID` array is searched for the matching ID and the corresponding bot value is selected.

## Writing test scenarios

Test scenarios are defined as YAML files in the `data/` folder. Each file can contain a single scenario object or a list of scenarios.

Example scenario structure:

```yaml
id: TC_SCHED_001
name: "Basic successful appointment booking"
type: "e2e_conversation"
objective: >
  You want to book a meeting.
  ...
reference_flow: >
  Agent: How can I help? -> Caller: Book a demo. -> ...

testData:
  name: "Alex Parker"
  email: "alex@staticso2.com"
  city: "Bangalore"
  phone: "9624474345"

expectedOutcomes:
  - id: intent_identified
    description: "The agent correctly identified the intent to book a demo."
    evaluator:
      type: regex
      pattern: "schedule|demo|software|book"
```

The framework’s `tests/conftest.py` automatically loads all YAML files in `data/*.yaml`.

## Running tests

Run the full test suite and generate reports:

```bash
export APP_ENV=App3
.venv/bin/pytest tests -vv -s --html=reports/qa_report.html --junitxml=reports/junit.xml
```

On Windows PowerShell:

```powershell
$env:APP_ENV = 'App3'
.venv\Scripts\pytest tests -vv -s --html=reports\qa_report.html --junitxml=reports\junit.xml
```

## Report output

- `reports/qa_report.html` — HTML report generated by `pytest-html`
- `reports/junit.xml` — JUnit-style test results for CI integration
- `transcripts/` — generated conversation transcripts

## Debugging configuration

The framework logs the resolved environment and selected bot ID during runtime. If you need to verify which credentials were chosen, check the test output.

## Jenkins CI/CD setup

Use Jenkins Credentials for secrets. Do not place `API_KEY`, `API_SECRET`, or the LiveKit URL directly in a Jenkinsfile, job command, or repository file.

### Jenkins prerequisites

- A Windows Jenkins agent with Python and network access to LiveKit.
- Pipeline or Freestyle support.
- Credentials Binding plugin.
- HTML Publisher plugin if the HTML report should be browsable in Jenkins.

### Create credentials

In **Manage Jenkins > Credentials**, create three **Secret text** credentials:

- `livekit-url` containing the target LiveKit WebSocket URL.
- `livekit-api-key` containing the API key.
- `livekit-api-secret` containing the API secret.

Do not print these variables in build steps. Rotate any credentials that were committed in `data/livekit_bot_config.json` before relying on Jenkins.

### Create a Pipeline job

1. Select **New Item > Pipeline**.
2. Choose **Pipeline script from SCM**, select Git, and configure the repository URL and branch.
3. Add the following pipeline script, replacing `YOUR_AGENT_LABEL` and credential IDs if needed:

```groovy
pipeline {
  agent { label 'YOUR_AGENT_LABEL' }

  parameters {
    choice(name: 'APP_ENV', choices: ['App3', 'App2', 'Orion'], description: 'Target environment')
  }

  stages {
    stage('Install') {
      steps {
        bat 'py -3 -m venv .venv'
        bat '.venv\\Scripts\\python -m pip install --upgrade pip'
        bat '.venv\\Scripts\\pip install -r requirements.txt'
      }
    }
    stage('Run QA tests') {
      steps {
        withCredentials([
          string(credentialsId: 'livekit-url', variable: 'LIVEKIT_URL'),
          string(credentialsId: 'livekit-api-key', variable: 'API_KEY'),
          string(credentialsId: 'livekit-api-secret', variable: 'API_SECRET')
        ]) {
          bat '.venv\\Scripts\\pytest tests -vv -s --html=reports\\qa_report.html --junitxml=reports\\junit.xml'
        }
      }
    }
  }

  post {
    always {
      junit 'reports/junit.xml'
      publishHTML(target: [
        allowMissing: true,
        alwaysLinkToLastBuild: true,
        keepAll: true,
        reportDir: 'reports',
        reportFiles: 'qa_report.html',
        reportName: 'QA HTML Report'
      ])
      archiveArtifacts artifacts: 'reports/**,transcripts/**', allowEmptyArchive: true
    }
  }
}
```

Jenkins automatically exposes the parameter as `APP_ENV`; the three bound credentials are available only during the test stage. The test fails if only part of the credential set is present.

### Manage the job safely

- Keep the job parameterized so a user selects `App3`, `App2`, or `Orion` per run.
- Restrict credential-management permissions to trusted administrators.
- Mask secrets in console output and never echo them in PowerShell or batch commands.
- Archive reports and transcripts, but review transcripts for personal data before sharing them broadly.
- Rotate LiveKit credentials periodically and immediately after accidental exposure.
- Use a separate Jenkins credential set per environment when environments have different access controls.

## Notes

- The framework is designed to allow QA and product teams to author scenarios in YAML without modifying Python code.
- If you add new YAML files, they are automatically included in test execution.
- Keep sensitive credentials out of source control if you use a shared repository. Prefer secret management or CI credentials.

## Contact

For questions about test case structure, environment mapping, or scenario creation, please reach out to the automation engineering team.
