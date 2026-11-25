pipeline {
  agent { label 'so-app2-win-es2' }

  tools {
    allure 'allure report generation'
  }

  parameters {
    choice(
      name: 'APP_ENV',
      choices: ['Prod', 'App3', 'App2', 'Orion'],
      description: 'Target environment'
    )
    string(
      name: 'TEST_REPEAT_COUNT',
      defaultValue: '1',
      description: 'Number of times to run each selected test'
    )
    string(
      name: 'SCENARIO_GROUP',
      defaultValue: 'simple-booking',
      description: 'Optional scenario folder/group under data/testDataToRun; blank runs all scenarios'
    )
  }

  environment {
    CI = 'true'
    VOICE_PROVIDER = 'azure-speech'
  }

  stages {
    stage('Clean Previous Test Reports') {
      steps {
        bat '''
          @echo off

          if exist "reports\\junit.xml" (
              del /F /Q "reports\\junit.xml"
              if errorlevel 1 exit /b 1
          )

          if exist "reports\\allure-results" (
              rmdir /S /Q "reports\\allure-results"
              if errorlevel 1 exit /b 1
          )

          exit /b 0
        '''
      }
    }

    stage('Install Python and Dependencies') {
      steps {
        bat '''
          @echo off
          setlocal

          set "TOOLS_DIR=%WORKSPACE%\\.job-tools"
          set "UV_DIR=%WORKSPACE%\\.job-tools\\uv"
          set "UV_PYTHON_INSTALL_DIR=%WORKSPACE%\\.job-tools\\python"
          set "UV_CACHE_DIR=%WORKSPACE%\\.job-tools\\uv-cache"

          if not exist "%UV_DIR%\\uv.exe" (
              echo Downloading uv...

              powershell -NoProfile -ExecutionPolicy Bypass -Command ^
                "$ErrorActionPreference='Stop';" ^
                "$ProgressPreference='SilentlyContinue';" ^
                "New-Item -ItemType Directory -Force -Path $env:UV_DIR | Out-Null;" ^
                "$zip=Join-Path $env:TOOLS_DIR 'uv.zip';" ^
                "Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile $zip;" ^
                "Expand-Archive -Path $zip -DestinationPath $env:UV_DIR -Force"

              if errorlevel 1 exit /b 1
          )

          echo Installing job-local Python 3.13...
          "%UV_DIR%\\uv.exe" python install 3.13
          if errorlevel 1 exit /b 1

          if exist ".venv" (
              echo Removing existing virtual environment...
              rmdir /S /Q ".venv"
              if errorlevel 1 exit /b 1
          )

          echo Creating Python 3.13 virtual environment...
          "%UV_DIR%\\uv.exe" venv ^
            --python 3.13 ^
            ".venv"

          if errorlevel 1 exit /b 1

          echo Installing Python dependencies...
          "%UV_DIR%\\uv.exe" pip install ^
            --python ".venv\\Scripts\\python.exe" ^
            -r requirements.txt

          if errorlevel 1 exit /b 1

          echo Verifying Python installation...
          ".venv\\Scripts\\python.exe" --version
          if errorlevel 1 exit /b 1

          echo Verifying Azure Speech SDK...
          ".venv\\Scripts\\python.exe" -c "import azure.cognitiveservices.speech as speechsdk; print('Azure Speech SDK version:', speechsdk.__version__)"
          if errorlevel 1 exit /b 1

          echo Verifying Edge TTS...
          ".venv\\Scripts\\python.exe" -c "import edge_tts; import imageio_ffmpeg; print('Edge TTS and bundled FFmpeg are available')"
          if errorlevel 1 exit /b 1

          echo Verifying Allure pytest adapter...
          ".venv\\Scripts\\python.exe" -c "import allure; import allure_pytest; print('Allure pytest adapter is available')"
          if errorlevel 1 exit /b 1

          endlocal
          exit /b 0
        '''
      }
    }

    stage('Setup AI Configuration') {
      steps {
        withCredentials([
          file(
            credentialsId: 'VOICE_TEST_ENV',
            variable: 'VOICE_ENV_FILE'
          )
        ]) {
          bat '''
            @echo off

            if not exist "%VOICE_ENV_FILE%" (
                echo ERROR: VOICE_TEST_ENV credential file was not found
                exit /b 1
            )

            copy /Y "%VOICE_ENV_FILE%" ".env" >nul

            if errorlevel 1 (
                echo ERROR: Failed to create .env file
                exit /b 1
            )

            if not exist ".env" (
                echo ERROR: .env file does not exist after copy
                exit /b 1
            )

            echo AI configuration loaded from VOICE_TEST_ENV
            exit /b 0
          '''
        }
      }
    }

    stage('Validate AI Configuration') {
      steps {
        bat '''
          @echo off

          ".venv\\Scripts\\python.exe" -c "from dotenv import dotenv_values; c=dotenv_values('.env'); required=['AZURE_OPENAI_API_KEY','AZURE_OPENAI_API_VERSION','AZURE_OPENAI_DEPLOYMENT_NAME','AZURE_OPENAI_ENDPOINT']; missing=[k for k in required if not c.get(k)]; assert not missing, 'Missing Azure variables: ' + ', '.join(missing); print('Azure OpenAI conversation configuration is available')"

          if errorlevel 1 exit /b 1
          exit /b 0
        '''
      }
    }

    stage('Resolve LiveKit Configuration') {
      steps {
        script {
          def liveKitConfigs = [
            App3: [
              url: 'wss://app2-7mtf3weu.livekit.cloud',
              credentialId: 'livekit-app3'
            ],
            App2: [
              url: 'wss://qaapp2-xn3x35vf.livekit.cloud',
              credentialId: 'livekit-app2'
            ],
            Orion: [
              url: 'wss://orion-qx3o4v38.livekit.cloud',
              credentialId: 'livekit-orion'
            ],
            Prod: [
              url: 'wss://prod-1825qoiq.livekit.cloud',
              credentialId: 'livekit-prod'
            ]
          ]

          def selectedConfig = liveKitConfigs[params.APP_ENV]

          if (!selectedConfig) {
            error("Unsupported environment: ${params.APP_ENV}")
          }

          env.APP_ENV = params.APP_ENV
          env.LIVEKIT_URL = selectedConfig.url
          env.LIVEKIT_CREDENTIAL_ID = selectedConfig.credentialId

          echo "Selected environment: ${env.APP_ENV}"
          echo "LiveKit URL: ${env.LIVEKIT_URL}"
          echo "Voice provider: ${env.VOICE_PROVIDER}"
        }
      }
    }

    stage('Check Azure Speech Access') {
      options {
        timeout(time: 2, unit: 'MINUTES')
      }

      steps {
        withCredentials([
          usernamePassword(
            credentialsId: 'AZURE_SPEECH_KEY',
            usernameVariable: 'AZURE_SPEECH_USER',
            passwordVariable: 'AZURE_SPEECH_KEY'
          )
        ]) {
          bat '''
            @echo off

            if not exist ".env" (
                echo ERROR: AI configuration file is missing
                exit /b 1
            )

            echo Checking Azure Speech credential binding...

            ".venv\\Scripts\\python.exe" -u -c "import os; from dotenv import dotenv_values; c=dotenv_values('.env'); k=os.getenv('AZURE_SPEECH_KEY','').strip(); f=(c.get('AZURE_SPEECH_KEY') or '').strip(); print('[CHECK] Jenkins Speech key present:', bool(k)); print('[CHECK] Jenkins Speech key length:', len(k)); print('[CHECK] .env Speech key present:', bool(f)); print('[CHECK] Jenkins Speech key matches .env key:', k == f if f else 'not applicable'); assert k, 'AZURE_SPEECH_KEY credential password is empty'; assert k != 'AZURE_SPEECH_KEY', 'AZURE_SPEECH_KEY contains the credential username instead of the resource key'; assert ':' not in k, 'AZURE_SPEECH_KEY appears to contain a combined username:password value'"

            if errorlevel 1 exit /b 1

            echo Checking effective Azure Speech endpoint...

            ".venv\\Scripts\\python.exe" -u -c "import os; from dotenv import dotenv_values, load_dotenv; from urllib.parse import urlsplit; c=dotenv_values('.env'); source='process environment' if 'AZURE_SPEECH_ENDPOINT' in os.environ else ('VOICE_TEST_ENV .env file' if c.get('AZURE_SPEECH_ENDPOINT') is not None else 'src/tts.py default'); load_dotenv('.env', override=False); e=os.getenv('AZURE_SPEECH_ENDPOINT','https://dev-speeck-service.cognitiveservices.azure.com/').strip(); u=urlsplit(e if '://' in e else 'https://' + e); print('[CHECK] Speech endpoint source:', source); print('[CHECK] Speech endpoint scheme:', u.scheme); print('[CHECK] Speech endpoint hostname:', u.hostname); print('[CHECK] Speech endpoint path:', u.path or '/'); assert e, 'AZURE_SPEECH_ENDPOINT is empty'; assert u.scheme in ('http','https') and u.hostname, 'AZURE_SPEECH_ENDPOINT must be an HTTP/HTTPS URL or hostname for the current src/tts.py implementation'; assert u.hostname == 'dev-speeck-service.cognitiveservices.azure.com', 'Effective Speech endpoint differs from the resource configured for this diagnostic'"

            if errorlevel 1 exit /b 1

            echo Testing Azure Speech REST access...

            ".venv\\Scripts\\python.exe" -u -c "import os, requests; k=os.environ['AZURE_SPEECH_KEY'].strip(); assert k, 'Speech key is empty'; url='https://dev-speeck-service.cognitiveservices.azure.com/tts/cognitiveservices/voices/list'; print('[SPEECH PROBE] Requesting voice list'); r=requests.get(url, headers={'Ocp-Apim-Subscription-Key': k}, timeout=30); print('[SPEECH PROBE] HTTP status:', r.status_code); print('[SPEECH PROBE] Response:', 'Voice list retrieved successfully' if r.status_code == 200 else r.text[:1500].replace(k, '<REDACTED>')); r.raise_for_status()"

            if errorlevel 1 (
                echo ERROR: Azure Speech REST access check failed. See SPEECH PROBE output above.
                exit /b 1
            )

            echo Azure Speech REST access check passed.
            echo SDK WebSocket synthesis will be exercised by the QA tests.
            exit /b 0
          '''
        }
      }
    }

    stage('Run QA Tests') {
      options {
        timeout(time: 4, unit: 'HOURS')
      }

      steps {
        script {
          def repeatCount = params.TEST_REPEAT_COUNT?.trim()

          if (!(repeatCount ==~ /^[1-9][0-9]*$/)) {
            error('TEST_REPEAT_COUNT must be a positive integer.')
          }

          env.TEST_REPEAT_COUNT = repeatCount

          def scenarioGroup = params.SCENARIO_GROUP?.trim()

          if (scenarioGroup && !(scenarioGroup ==~ /^[A-Za-z0-9_-]+$/)) {
            error('SCENARIO_GROUP may contain only letters, numbers, hyphens, and underscores.')
          }

          env.SCENARIO_GROUP = scenarioGroup ?: ''
          env.SCENARIO_GROUP_ARGS = scenarioGroup ? "--scenario-group ${scenarioGroup}" : ''
        }

        withCredentials([
          usernamePassword(
            credentialsId: env.LIVEKIT_CREDENTIAL_ID,
            usernameVariable: 'API_KEY',
            passwordVariable: 'API_SECRET'
          ),
          usernamePassword(
            credentialsId: 'AZURE_SPEECH_KEY',
            usernameVariable: 'AZURE_SPEECH_USER',
            passwordVariable: 'AZURE_SPEECH_KEY'
          )
        ]) {
          bat '''
            @echo off

            if not exist ".env" (
                echo ERROR: AI configuration file is missing
                exit /b 1
            )

            if not exist "reports" (
                mkdir "reports"
                if errorlevel 1 exit /b 1
            )

            echo Running LiveKit tests for environment: %APP_ENV%
            echo Voice provider: %VOICE_PROVIDER%

            if "%SCENARIO_GROUP_ARGS%"=="" (
                echo Scenario group: all
            ) else (
                echo Scenario group: %SCENARIO_GROUP%
            )

            echo Allure reporting and Python output capture are enabled.

            ".venv\\Scripts\\python.exe" -m pytest tests -m "not config" ^
              -vv ^
              --count=%TEST_REPEAT_COUNT% ^
              %SCENARIO_GROUP_ARGS% ^
              --capture=tee-sys ^
              --log-level=INFO ^
              --junitxml=reports/junit.xml ^
              --alluredir=reports/allure-results ^
              --clean-alluredir

            exit /b %ERRORLEVEL%
          '''
        }
      }

      post {
        always {
          script {
            if (fileExists('reports/allure-results')) {
              allure(
                commandline: 'allure report generation',
                includeProperties: false,
                jdk: '',
                reportBuildPolicy: 'ALWAYS',
                results: [[path: 'reports/allure-results']]
              )
            } else {
              echo 'No Allure results generated; skipping publication.'
            }
          }
        }
      }
    }
  }

  post {
    always {
      bat '''
        @echo off

        if exist ".env" (
            del /F /Q ".env"
            if errorlevel 1 exit /b 1
            echo Temporary AI configuration file removed
        )

        exit /b 0
      '''

      archiveArtifacts(
        artifacts: 'reports/**,transcripts/**',
        allowEmptyArchive: true
      )

      script {
        if (fileExists('reports/junit.xml')) {
          catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
            junit(
              testResults: 'reports/junit.xml',
              allowEmptyResults: false
            )
          }
        } else {
          echo 'No JUnit report generated; tests may have been skipped because an earlier stage failed.'
        }
      }
    }
  }
}