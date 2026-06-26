// DB-CPT-RHEL — PostgreSQL + HammerDB benchmark pipeline for Jenkins.
//
// Parameters are injected by the JobDSL seed (see src/jobs/DbCptRhelJob.groovy)
// or passed manually via "Build with Parameters" in the Jenkins UI.
//
// Required credentials (Jenkins → Manage Credentials):
//   PGPASSWORD  — Secret text; PostgreSQL password for pass_or_fail + upload.
//   SSH_KEY     — SSH private key for Ansible to reach bench/client hosts.

pipeline {
    agent { label params.JENKINS_AGENT ?: 'perf-controller' }

    options {
        timestamps()
        ansiColor('xterm')
        timeout(time: 8, unit: 'HOURS')
        buildDiscarder(logRotator(daysToKeepStr: '90', numToKeepStr: '100'))
    }

    parameters {
        choice(name: 'MODE',
               choices: ['compare', 'baseline', 'matrix'],
               description: 'baseline = seed history (PASS); compare/matrix = regression check.')
        string(name: 'RHEL_VERSION',
               defaultValue: '9.0',
               description: 'Bench RHEL version for os-setup + benchmark (e.g. 9.0, 9.4, 9.7).')
        string(name: 'HARDWARE',
               defaultValue: 'r650',
               description: 'Hardware cohort tag stored in master cpt_profile (r640, r650, …).')
        string(name: 'VUS',
               defaultValue: '',
               description: 'Comma-separated VU list (leave empty to use inventory hammerdb_virtual_users_matrix).')
        string(name: 'REPEATS',
               defaultValue: '1',
               description: 'Repeats per VU point.')
        string(name: 'LABEL',
               defaultValue: '',
               description: 'Optional free-form profile label (e.g. staging, nightly).')
        booleanParam(name: 'SKIP_OS_SETUP',
                     defaultValue: false,
                     description: 'Skip os-setup.yaml (bench hosts already on correct RHEL).')
        booleanParam(name: 'SKIP_SETUP',
                     defaultValue: false,
                     description: 'Skip setup.yaml (bench + client already provisioned).')
        string(name: 'JENKINS_AGENT',
               defaultValue: 'perf-controller',
               description: 'Jenkins agent label to run on.')
    }

    environment {
        PGPASSWORD         = credentials('PGPASSWORD')
        CPT_ARTIFACT_ROOT  = "${env.WORKSPACE}/ARTIFACTS/DB-CPT-RHEL"
        ANSIBLE_FORCE_COLOR = '1'
        ANSIBLE_STDOUT_CALLBACK = 'yaml'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Install dependencies') {
            steps {
                sh '''
                    python3 -m venv .venv
                    . .venv/bin/activate
                    pip install --upgrade pip
                    pip install -r requirements.txt
                '''
                sh '''
                    . .venv/bin/activate
                    ansible-galaxy collection install -r requirements.yml --force
                '''
            }
        }

        stage('Prepare configs') {
            steps {
                sh '''
                    if [ ! -f pass_or_fail_cfg.yaml ]; then
                        cp pass_or_fail_cfg.yaml.example pass_or_fail_cfg.yaml
                    fi
                    if [ ! -f archive_cfg.yaml ]; then
                        cp archive_cfg.yaml.example archive_cfg.yaml
                    fi
                '''
            }
        }

        stage('Run benchmark') {
            steps {
                sh """
                    . .venv/bin/activate
                    set -x

                    ARGS="${params.MODE} --rhel ${params.RHEL_VERSION}"

                    if [ -n "${params.HARDWARE}" ]; then
                        ARGS="\${ARGS} --hardware ${params.HARDWARE}"
                    fi

                    if [ -n "${params.VUS}" ]; then
                        ARGS="\${ARGS} --vus ${params.VUS}"
                    fi

                    if [ -n "${params.LABEL}" ]; then
                        ARGS="\${ARGS} --label ${params.LABEL}"
                    fi

                    if [ "${params.REPEATS}" != "1" ] && [ -n "${params.REPEATS}" ]; then
                        ARGS="\${ARGS} --repeats ${params.REPEATS}"
                    fi

                    if [ "${params.SKIP_OS_SETUP}" = "true" ]; then
                        ARGS="\${ARGS} --skip-os-setup"
                    fi

                    if [ "${params.SKIP_SETUP}" = "true" ]; then
                        ARGS="\${ARGS} --skip-setup"
                    fi

                    echo "========== cpt-run.sh \${ARGS} =========="
                    ./scripts/cpt-run.sh \${ARGS}
                """
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'results/**/*.json, results/**/*.log',
                             allowEmptyArchive: true,
                             fingerprint: true

            sh '''
                if [ -d "${CPT_ARTIFACT_ROOT}" ]; then
                    echo "Artifacts archived to ${CPT_ARTIFACT_ROOT}"
                    ls -lhR "${CPT_ARTIFACT_ROOT}" || true
                fi
            '''
        }
        failure {
            echo "DB-CPT-RHEL pipeline failed for RHEL ${params.RHEL_VERSION} (${params.MODE})"
        }
        cleanup {
            cleanWs(deleteDirs: true,
                    patterns: [[pattern: '.venv/**', type: 'INCLUDE'],
                               [pattern: 'collections/**', type: 'INCLUDE']])
        }
    }
}
