#!/usr/bin/env bash

set -eo pipefail

CMD=$@
DOCKER_ENV_DIR=$(readlink -f "$0")
DOCKER_ENV_DIR=$(dirname "${DOCKER_ENV_DIR}")
DOCKER_REPO=scylladb/hydra
DOCKER_REGISTRY=docker.io
SCT_DIR=$(dirname "${DOCKER_ENV_DIR}")
SCT_DIR=$(dirname "${SCT_DIR}")
VERSION=v$(cat "${DOCKER_ENV_DIR}/version")
HOST_NAME=SCT-CONTAINER
RUN_BY_USER=$(python3 "${SCT_DIR}/sdcm/utils/get_username.py")
USER_ID=$(id -u "${USER}")
HOME_DIR=${HOME}

CREATE_RUNNER_INSTANCE=""
RUNNER_IP_FILE="${SCT_DIR}/sct_runner_ip"
RUNNER_IP=""
RUNNER_CMD=""
AWS_MOCK=""

HYDRA_DRY_RUN=""
HYDRA_HELP=""

export SCT_TEST_ID=${SCT_TEST_ID:-$(uuidgen)}
export GIT_USER_EMAIL=$(git config --get user.email)

# Hydra arguments parsing

SCT_ARGUMENTS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --execute-on-new-runner)
            CREATE_RUNNER_INSTANCE="1"
            shift
            ;;
        --execute-on-runner)
            RUNNER_IP="$2"
            shift 2
            ;;
        --aws-mock)
            AWS_MOCK="1"
            shift
            ;;
        --dry-run-hydra)
            HYDRA_DRY_RUN="1"
            shift
            ;;
        --install-package-from-directory)
            SCT_ARGUMENTS+=("$1" "$2")
            shift 2
            ;;
        --install-bash-completion)
            SCT_ARGUMENTS+=("$1")
            shift
            ;;
        --help)
            HYDRA_HELP="1"
            SCT_ARGUMENTS+=("$1")
            shift
            ;;
        -*)
            echo "Unknown argument '$1'"
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

# Hydra command arguments line parsing

HYDRA_COMMAND=()

while [[ $# -gt 0 ]]; do
    case $1 in
        -b|--backend)
            SCT_CLUSTER_BACKEND="$2"
            HYDRA_COMMAND+=("$1" "$2")
            shift 2
            ;;
        --help)
            HYDRA_HELP="1"
            HYDRA_COMMAND+=("$1")
            shift
            ;;
        *)
            HYDRA_COMMAND+=("$1")
            shift
            ;;
    esac
done

if [[ -n "${CREATE_RUNNER_INSTANCE}" ]]; then
    if [[ -n "${RUNNER_IP}" ]]; then
        echo "Can't use '--execute-on-new-runner' and '--execute-on-runner IP' options simultaneously"
        exit 1
    fi
    if [[ -f "${RUNNER_IP_FILE}" ]]; then
        RUNNER_IP=$(<"${RUNNER_IP_FILE}")
        echo "Looks like there is another SCT runner launched already (Public IP: ${RUNNER_IP})"
        echo "Please, delete '${RUNNER_IP_FILE}' file first and try again."
        echo "Or use 'hydra --execute-on-runner ${RUNNER_IP} ...' to run command on existing runner"
        exit 1
    fi
    echo ">>> Create a new SCT runner instance"
    echo
    if [[ -z "${HYDRA_DRY_RUN}" ]]; then
        HYDRA=$0
    else
        HYDRA="echo $0"
    fi

    if [[ -n "${RESTORED_TEST_ID}" ]]; then
        RESTORED_TEST_ID="--restored-test-id ${RESTORED_TEST_ID}"
    else
        RESTORED_TEST_ID=""
    fi

    ${HYDRA} create-runner-instance \
      --cloud-provider aws \
      --region "${RUNNER_REGION:-us-east-1}" \
      --availability-zone "${RUNNER_AZ:-a}" \
      --test-id "${SCT_TEST_ID}" \
      --duration "${RUNNER_DURATION:-1440}" \
      --restore-monitor "${RESTORE_MONITOR_RUNNER:-False}" \
      ${RESTORED_TEST_ID}

    if [[ -z "${HYDRA_DRY_RUN}" ]]; then
        RUNNER_IP=$(<"${RUNNER_IP_FILE}")
    else
        RUNNER_IP="127.0.0.1"  # set it for testing purpose.
    fi
    echo
    echo ">>> Run hydra command on the new SCT runner w/ public IP: ${RUNNER_IP}"
    echo
fi

# if running on Build server
if [[ ${USER} == "jenkins" ]]; then
    echo "Running on Build Server..."
    HOST_NAME=`hostname`
else
    TTY_STDIN="-it"
    TPUT_OPTIONS=""
    [[ -z "$TERM" || "$TERM" == 'dumb' ]] && TPUT_OPTIONS="-T xterm-256color"
    TERM_SET_SIZE="export COLUMNS=`tput $TPUT_OPTIONS cols`; export LINES=`tput $TPUT_OPTIONS lines`;"
fi

if which docker >/dev/null 2>&1 ; then
  tool=${HYDRA_TOOL-docker}
elif which podman >/dev/null 2>&1 ; then
  tool=${HYDRA_TOOL-podman}
else
  die "Please make sure you install either podman or docker on this machine to run hydra"
fi

if [[ ${USER} == "jenkins" || -z "`$tool images ${DOCKER_REPO}:${VERSION} -q`" ]]; then
    echo "Pull version $VERSION from Docker Hub..."
    $tool pull ${DOCKER_REGISTRY}/${DOCKER_REPO}:${VERSION}
else
    echo "There is ${DOCKER_REPO}:${VERSION} in local cache, using it."
fi

# Check for SSH keys
if [[ -z "$HYDRA_HELP" ]]; then
    if [ -z "$HYDRA_DRY_RUN" ]; then
        ${SCT_DIR}/get-qa-ssh-keys.sh
    else
        echo ${SCT_DIR}/get-qa-ssh-keys.sh
    fi
fi

if [ -z "$HYDRA_DRY_RUN" ]; then
    DOCKER_GROUP_ARGS=()
else
    # Setting it for testing purpose
    DOCKER_GROUP_ARGS='--group-add 1 --group-add 2 --group-add 3'
fi

DOCKER_ADD_HOST_ARGS=()

# export all SCT_* env vars into the docker run
SCT_OPTIONS=$(env | sed -n 's/^\(SCT_[^=]\+\)=.*/--env \1/p')

# export all PYTEST_* env vars into the docker run
PYTEST_OPTIONS=$(env | sed -n 's/^\(PYTEST_[^=]\+\)=.*/--env \1/p')

# export all BUILD_* env vars into the docker run
BUILD_OPTIONS=$(env | sed -n 's/^\(BUILD_[^=]\+\)=.*/--env \1/p')

# export all AWS_* env vars into the docker run
AWS_OPTIONS=$(env | sed -n 's/^\(AWS_[^=]\+\)=.*/--env \1/p')

# export all JENKINS_* env vars into the docker run
JENKINS_OPTIONS=$(env | sed -n 's/^\(JENKINS_[^=]\+\)=.*/--env \1/p')

is_podman="$($tool --help | { grep -o podman || :; })"
docker_common_args=()

function EPHEMERAL_PORT() {
    LOW_BOUND=49152
    RANGE=16384
    while true; do
        CANDIDATE=$[$LOW_BOUND + ($RANDOM % $RANGE)]
        (echo "" >/dev/tcp/127.0.0.1/${CANDIDATE}) >/dev/null 2>&1
        if [ $? -ne 0 ]; then
            echo $CANDIDATE
            break
        fi
    done
}

function run_in_docker () {
    CMD_TO_RUN=$1
    REMOTE_DOCKER_HOST=$2
    if [ -z "$is_podman" ]; then
        docker_common_args+=(
           -v /var/run:/run
           )
    else
        PODMAN_PORT=$(EPHEMERAL_PORT)
        podman system service -t 0 tcp:localhost:${PODMAN_PORT} &
        trap "exit" INT TERM
        trap "kill 0" EXIT
        docker_common_args+=(
          -v $SCT_DIR/docker/docker_mocked_as_podman:/usr/local/bin/docker
          --userns=keep-id
          -e DOCKER_HOST=tcp://localhost:$PODMAN_PORT
        )
    fi

    echo "Going to run '${CMD_TO_RUN}'..."
    $([[ -n "$HYDRA_DRY_RUN" ]] && echo echo) \
    $tool ${REMOTE_DOCKER_HOST} run --rm ${TTY_STDIN} --privileged \
        -h ${HOST_NAME} \
        -l "TestId=${SCT_TEST_ID}" \
        -l "RunByUser=${RUN_BY_USER}" \
        -v "${SCT_DIR}:${SCT_DIR}" \
        -v /tmp:/tmp \
        -v /var/tmp:/var/tmp \
        -v "${HOME_DIR}:${HOME_DIR}" \
        -w "${SCT_DIR}" \
        -e JOB_NAME="${JOB_NAME}" \
        -e BUILD_URL="${BUILD_URL}" \
        -e BUILD_NUMBER="${BUILD_NUMBER}" \
        -e _SCT_BASE_DIR="${SCT_DIR}" \
        -e GIT_USER_EMAIL \
        -e RUNNER_IP \
        -u ${USER_ID} \
        -v /sys/fs/cgroup:/sys/fs/cgroup:ro \
        -v /etc/passwd:/etc/passwd:ro \
        -v /etc/group:/etc/group:ro \
        -v /etc/sudoers:/etc/sudoers:ro \
        -v /etc/sudoers.d/:/etc/sudoers.d:ro \
        -v /etc/shadow:/etc/shadow:ro \
        ${DOCKER_GROUP_ARGS[@]} \
        ${DOCKER_ADD_HOST_ARGS[@]} \
        ${docker_common_args[@]} \
        ${SCT_OPTIONS} \
        ${PYTEST_OPTIONS} \
        ${BUILD_OPTIONS} \
        ${JENKINS_OPTIONS} \
        ${AWS_OPTIONS} \
        --env GIT_BRANCH \
        --env CHANGE_TARGET \
        --net=host \
        --name="${SCT_TEST_ID}_$(date +%s)" \
        ${DOCKER_REPO}:${VERSION} \
        /bin/bash -c "${PREPARE_CMD}; ${TERM_SET_SIZE} eval '${CMD_TO_RUN}'"
}

if [[ -n "$RUNNER_IP" ]]; then
    export RUNNER_IP  # make it available inside SCT code.

    if [[ ! "$RUNNER_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "=========================================================================================================="
        echo "Invalid IP provided for '--execute-on-runner'. Run 'hydra create-runner-instance' or check ./sct_runner_ip"
        echo "=========================================================================================================="
        exit 2
    fi
    echo "SCT Runner IP: $RUNNER_IP"

    if [ -z "$HYDRA_DRY_RUN" ]; then
        eval $(ssh-agent)
    else
        echo 'eval $(ssh-agent)'
    fi

    function clean_ssh_agent {
        echo "Cleaning SSH agent"
        if [ -z "$HYDRA_DRY_RUN" ]; then
            eval $(ssh-agent -k)
        else
            echo 'eval $(ssh-agent -k)'
        fi
    }

    trap clean_ssh_agent EXIT

    if [ -z "$HYDRA_DRY_RUN" ]; then
        ssh-add ~/.ssh/scylla-qa-ec2
        ssh-add ~/.ssh/scylla-test
    else
        echo ssh-add ~/.ssh/scylla-qa-ec2
        echo ssh-add ~/.ssh/scylla-test
    fi

    echo "Going to run a Hydra commands on SCT runner '$RUNNER_IP'..."
    HOME_DIR="/home/ubuntu"

    echo "Syncing ${SCT_DIR} to the SCT runner instance..."
    if [ -z "$HYDRA_DRY_RUN" ]; then
        ssh-keygen -R "$RUNNER_IP" || true
        rsync -ar -e 'ssh -o StrictHostKeyChecking=no' --delete ${SCT_DIR} ubuntu@${RUNNER_IP}:/home/ubuntu/
    else
        echo "ssh-keygen -R \"$RUNNER_IP\" || true"
        echo "rsync -ar -e 'ssh -o StrictHostKeyChecking=no' --delete ${SCT_DIR} ubuntu@${RUNNER_IP}:/home/ubuntu/"
    fi
    if [[ -z "$AWS_OPTIONS" ]]; then
        echo "AWS credentials were not passed using AWS_* environment variables!"
        echo "Checking if ~/.aws/credentials exists..."
        if [ ! -f ~/.aws/credentials ]; then
            echo "Can't run SCT without AWS credentials!"
            exit 1
        fi
        echo "AWS credentials file found. Syncing to SCT Runner..."
        if [ -z "$HYDRA_DRY_RUN" ]; then
            rsync -ar -e 'ssh -o StrictHostKeyChecking=no' --delete ~/.aws ubuntu@${RUNNER_IP}:/home/ubuntu/
        else
            echo "rsync -ar -e 'ssh -o StrictHostKeyChecking=no' --delete ~/.aws ubuntu@${RUNNER_IP}:/home/ubuntu/"
        fi
    else
        echo "AWS_* environment variables found and will passed to Hydra container."
    fi

    # Only copy GCE credential for GCE backend
    if [[ "${SCT_CLUSTER_BACKEND}" =~ "gce" || "${SCT_CLUSTER_BACKEND}" =~ "gke" ]]; then
        if [ -f ~/.google_libcloud_auth.skilled-adapter-452 ]; then
            echo "GCE credentials file found. Syncing to SCT Runner..."
            if [ -z "$HYDRA_DRY_RUN" ]; then
                rsync -ar -e 'ssh -o StrictHostKeyChecking=no' --delete ~/.google_libcloud_auth.skilled-adapter-452 ubuntu@${RUNNER_IP}:/home/ubuntu/
            else
                echo "rsync -ar -e 'ssh -o StrictHostKeyChecking=no' --delete ~/.google_libcloud_auth.skilled-adapter-452 ubuntu@${RUNNER_IP}:/home/ubuntu/"
            fi
        else
            echo "GCE backend is used, but no gcloud token found !!!"
        fi
    fi

    SCT_DIR="/home/ubuntu/scylla-cluster-tests"
    HOST_NAME="ip-${RUNNER_IP//./-}"
    USER_ID=1000
    RUNNER_CMD="ssh -o StrictHostKeyChecking=no ubuntu@${RUNNER_IP}"
    DOCKER_HOST="-H ssh://ubuntu@${RUNNER_IP}"
fi

if [ -z "${DOCKER_GROUP_ARGS[@]}" ]; then
    for gid in $(${RUNNER_CMD} id -G); do
        DOCKER_GROUP_ARGS+=(--group-add "$gid")
    done
fi

PREPARE_CMD="test"

if [[ -n "${AWS_MOCK}" ]]; then
    if [[ -z "${HYDRA_DRY_RUN}" ]]; then
        AWS_MOCK_IP=$(${RUNNER_CMD} cat aws_mock_ip)
        MOCKED_HOSTS=$(${RUNNER_CMD} openssl s_client -connect "${AWS_MOCK_IP}:443" </dev/null 2>/dev/null \
                       | openssl x509 -noout -text \
                       | grep -Po '(?<=DNS:)[^,]+')
    else
        AWS_MOCK_IP=127.0.0.1
        MOCKED_HOSTS="aws-mock.itself scylla-qa-keystore.s3.amazonaws.com ec2.eu-west-2.amazonaws.com"
    fi
    for host in ${MOCKED_HOSTS}; do
        echo "Mock requests to ${host} using ${AWS_MOCK_IP}"
        DOCKER_ADD_HOST_ARGS+=(--add-host "${host}:${AWS_MOCK_IP}")
    done
    PREPARE_CMD+="; curl -sSk https://aws-mock.itself/install-ca.sh | bash"
fi

if [[ -z "${HYDRA_HELP}" ]]; then
    PREPARE_CMD+="; ${SCT_DIR}/get-qa-ssh-keys.sh"
fi

COMMAND=${HYDRA_COMMAND[0]}

if [[ "$COMMAND" == *'bash'* ]] || [[ "$COMMAND" == *'python'* ]]; then
    CMD=${HYDRA_COMMAND[@]}
else
    CMD="./sct.py ${SCT_ARGUMENTS[@]} ${HYDRA_COMMAND[@]}"
fi

run_in_docker "${CMD}" "${DOCKER_HOST}"
