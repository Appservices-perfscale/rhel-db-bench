// JobDSL seed for DB-CPT-RHEL — creates the Jenkins job automatically.
//
// Add this file (or the src/jobs/ folder) to your ci-configs seed job so it
// picks up DbCptRhelJob.groovy on the next scan.
//
// The generated job points at jenkins/DB-CPT-RHEL.groovy in the same repo.

def gitRepo   = 'https://github.com/kmagar25/rhel-db-bench.git'
def gitBranch = 'main'

pipelineJob('DB-CPT-RHEL') {
    description('''\
        PostgreSQL + HammerDB cross-RHEL performance regression pipeline.
        <br>Runs <code>cpt-run.sh</code> (baseline / compare / matrix) on scale-lab hosts,
        uploads results to PostgreSQL, and reports PASS/FAIL via OPL pass_or_fail.
    '''.stripIndent())

    logRotator {
        daysToKeep(90)
        numToKeep(100)
    }

    parameters {
        choiceParam('MODE', ['compare', 'baseline', 'matrix'],
                    'baseline = seed history (PASS); compare/matrix = regression check.')
        stringParam('RHEL_VERSION', '9.0',
                    'Bench RHEL version for os-setup + benchmark (e.g. 9.0, 9.4, 9.7).')
        stringParam('HARDWARE', 'r650',
                    'Hardware cohort tag stored in master cpt_profile (r640, r650, …).')
        stringParam('VUS', '',
                    'Comma-separated VU list (leave empty to use inventory hammerdb_virtual_users_matrix).')
        stringParam('REPEATS', '1',
                    'Repeats per VU point.')
        stringParam('LABEL', '',
                    'Optional free-form profile label (e.g. staging, nightly).')
        booleanParam('SKIP_OS_SETUP', false,
                     'Skip os-setup.yaml (bench hosts already on correct RHEL).')
        booleanParam('SKIP_SETUP', false,
                     'Skip setup.yaml (bench + client already provisioned).')
        stringParam('JENKINS_AGENT', 'perf-controller',
                    'Jenkins agent label to run on.')
    }

    definition {
        cpsScm {
            scm {
                git {
                    remote {
                        url(gitRepo)
                        credentials('github-token')
                    }
                    branches(gitBranch)
                }
            }
            scriptPath('jenkins/DB-CPT-RHEL.groovy')
        }
    }
}
