pipeline {
  agent { label 'so-app2-win-es2' }

  parameters {
    choice(
      name: 'APP_ENV',
      choices: ['App3', 'App2', 'Orion'],
      description: 'Target environment'
    )
  }

  environment {
    CI = 'true'
    CONVERSATION_AI = 'openai'
  }

  stages {
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
                "$ProgressPreference='SilentlyContinue';" ^
                "New-Item -ItemType Directory -Force -Path '%UV_DIR%' | Out-Null;" ^
                "Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '%TEMP%\\uv.zip';" ^
                "Expand-Archive -Path '%TEMP%\\uv.zip' -DestinationPath '%UV_DIR%' -Force"

              if errorlevel 1 exit /b 1
          )

          echo Installing job-local Python 3.13...
          "%UV_DIR%\\uv.exe" python install 3.13
          if errorlevel 1 exit /b 1

          if exist ".venv" (
              echo Removing existing virtual environment...
              rmdir /s /q ".venv"
          )

          echo Creating Python 3.13 virtual environment...
          "%UV_DIR%\\uv.exe" venv --python 3.13 ".venv"
          if errorlevel 1 exit /b 1

          echo Installing Python dependencies...
          "%UV_DIR%\\uv.exe" pip install ^
            --python ".venv\\Scripts\\python.exe" ^
            -r requirements.txt
          if errorlevel 1 exit /b 1

          echo Verifying Python installation...
          ".venv\\Scripts\\python.exe" --version
          if errorlevel 1 exit /b 1

          ".venv\\Scripts\\python.exe" -c "import asyncio; print(asyncio.Queue[str])"
          if errorlevel 1 exit /b 1

          endlocal
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

            echo AI configuration loaded from VOICE_TEST_ENV
          '''
        }
      }
    }

    stage('Resolve LiveKit Configuration') {
      steps {
        script {
          def liveKitConfigs = [
            App3: [
              url          : 'wss://app2-7mtf3weu.livekit.cloud',
              credentialId : 'livekit-app3'
            ],
            App2: [
              url          : 'wss://qaapp2-xn3x35vf.livekit.cloud',
              credentialId : 'livekit-app2'
            ],
            Orion: [
              url          : 'wss://orion-qx3o4v38.livekit.cloud',
              credentialId : 'livekit-orion'
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
          echo "Conversation AI provider: ${env.CONVERSATION_AI}"
        }
      }
    }

    stage('Run QA Tests') {
      steps {
        withCredentials([
          usernamePassword(
            credentialsId: env.LIVEKIT_CREDENTIAL_ID,
            usernameVariable: 'API_KEY',
            passwordVariable: 'API_SECRET'
          )
        ]) {
          bat '''
            @echo off

            if not exist ".env" (
                echo ERROR: AI configuration file is missing
                exit /b 1
            )

            if not exist "reports" mkdir "reports"

            echo Running tests for environment: %APP_ENV%
            echo Conversation AI provider: %CONVERSATION_AI%

            ".venv\\Scripts\\python.exe" -m pytest tests ^
              -vv ^
              -s ^
              --html="reports\\qa_report.html" ^
              --junitxml="reports\\junit.xml"
          '''
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
            echo Temporary AI configuration file removed
        )
      '''

      junit(
        testResults: 'reports/junit.xml',
        allowEmptyResults: true
      )

      publishHTML(target: [
        allowMissing: true,
        alwaysLinkToLastBuild: true,
        keepAll: true,
        reportDir: 'reports',
        reportFiles: 'qa_report.html',
        reportName: 'QA HTML Report'
      ])

      archiveArtifacts(
        artifacts: 'reports/**,transcripts/**',
        allowEmptyArchive: true
      )
    }
  }
}