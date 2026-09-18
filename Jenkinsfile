pipeline {
  agent { label 'so-app2-win-es2' }

  parameters {
    choice(name: 'APP_ENV', choices: ['App3', 'App2', 'Orion'], description: 'Target environment')
  }

  stages {
    stage('Install') {
      steps {
        bat 'py -3.13 --version'
        bat 'py -3.13 -c "import asyncio; print(asyncio.Queue[str])"'
        bat 'if exist .venv rmdir /s /q .venv'
        bat 'py -3.13 -m venv .venv'
        bat '.venv\\Scripts\\python --version'
        bat '.venv\\Scripts\\python -m pip install --upgrade pip'
        bat '.venv\\Scripts\\pip install -r requirements.txt'
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
                        Orion: [
                            url          : 'wss://orion-qx3o4v38.livekit.cloud',
                            credentialId : 'livekit-orion'
                        ],
                        App2: [
                            url          : 'wss://qaapp2-xn3x35vf.livekit.cloud',
                            credentialId : 'livekit-app2'
                        ],
                        Prod: [
                            url          : 'wss://prod-1825qoiq.livekit.cloud',
                            credentialId : 'livekit-prod'
                        ]
                    ]

                    def selectedConfig = liveKitConfigs[params.APP_ENV]

                    if (!selectedConfig) {
                      error("Unsupported environment: ${params.APP_ENV}")
                    }

                    env.APP_ENV = params.APP_ENV
                    env.LIVEKIT_URL = selectedConfig.url
                    env.LIVEKIT_CREDENTIAL_ID = selectedConfig.credentialId

                    echo "Selected environment: ${params.APP_ENV}"
                    echo "LiveKit URL: ${env.LIVEKIT_URL}"
                }
            }
        }
    stage('Run QA tests') {
      steps {
        withCredentials([
          usernamePassword(
                        credentialsId: env.LIVEKIT_CREDENTIAL_ID,
                  usernameVariable: 'API_KEY',
                  passwordVariable: 'API_SECRET'
                    )
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