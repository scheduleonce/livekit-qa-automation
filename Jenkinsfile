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