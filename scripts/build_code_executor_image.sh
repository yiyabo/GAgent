#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_TAG="${CODE_EXECUTOR_DOCKER_IMAGE:-gagent-python-runtime:latest}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-docker/code_executor/Dockerfile}"
BUILD_PROGRESS="${BUILD_PROGRESS:-plain}"
REMOTE_MICROMAMBA_BASE_IMAGE="${REMOTE_MICROMAMBA_BASE_IMAGE:-mambaorg/micromamba:2.5.0}"
LOCAL_MICROMAMBA_BASE_IMAGE="${LOCAL_MICROMAMBA_BASE_IMAGE:-gagent-local-micromamba-base:2.5.0}"

export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"

_scutil_proxy_value() {
  local key="$1"
  scutil --proxy 2>/dev/null | awk -v key="${key}" '$1 == key && $2 == ":" { print $3; exit }'
}

_rewrite_proxy_for_build_arg() {
  local value="$1"
  case "${value}" in
    http://127.0.0.1:*|http://localhost:*|https://127.0.0.1:*|https://localhost:*|socks5://127.0.0.1:*|socks5://localhost:*)
      echo "${value/127.0.0.1/host.docker.internal}" | sed 's#localhost#host.docker.internal#'
      ;;
    *)
      echo "${value}"
      ;;
  esac
}

if [[ "$(uname -s)" == "Darwin" ]]; then
  if [[ -z "${HTTP_PROXY:-}${http_proxy:-}" ]]; then
    http_enable="$(_scutil_proxy_value HTTPEnable)"
    http_host="$(_scutil_proxy_value HTTPProxy)"
    http_port="$(_scutil_proxy_value HTTPPort)"
    if [[ "${http_enable}" == "1" && -n "${http_host}" && -n "${http_port}" ]]; then
      export HTTP_PROXY="http://${http_host}:${http_port}"
      export http_proxy="${HTTP_PROXY}"
    fi
  fi

  if [[ -z "${HTTPS_PROXY:-}${https_proxy:-}" ]]; then
    https_enable="$(_scutil_proxy_value HTTPSEnable)"
    https_host="$(_scutil_proxy_value HTTPSProxy)"
    https_port="$(_scutil_proxy_value HTTPSPort)"
    if [[ "${https_enable}" == "1" && -n "${https_host}" && -n "${https_port}" ]]; then
      export HTTPS_PROXY="http://${https_host}:${https_port}"
      export https_proxy="${HTTPS_PROXY}"
    fi
  fi

  if [[ -z "${ALL_PROXY:-}${all_proxy:-}" ]]; then
    socks_enable="$(_scutil_proxy_value SOCKSEnable)"
    socks_host="$(_scutil_proxy_value SOCKSProxy)"
    socks_port="$(_scutil_proxy_value SOCKSPort)"
    if [[ "${socks_enable}" == "1" && -n "${socks_host}" && -n "${socks_port}" ]]; then
      export ALL_PROXY="socks5://${socks_host}:${socks_port}"
      export all_proxy="${ALL_PROXY}"
    fi
  fi
fi

if ! docker image inspect "${LOCAL_MICROMAMBA_BASE_IMAGE}" >/dev/null 2>&1; then
  if docker image inspect "${REMOTE_MICROMAMBA_BASE_IMAGE}" >/dev/null 2>&1; then
    docker tag "${REMOTE_MICROMAMBA_BASE_IMAGE}" "${LOCAL_MICROMAMBA_BASE_IMAGE}"
  else
    docker pull "${REMOTE_MICROMAMBA_BASE_IMAGE}"
    docker tag "${REMOTE_MICROMAMBA_BASE_IMAGE}" "${LOCAL_MICROMAMBA_BASE_IMAGE}"
  fi
fi

build_args=(
  --load
  --platform "${DOCKER_PLATFORM}"
  --progress "${BUILD_PROGRESS}"
  -t "${IMAGE_TAG}"
  -f "${ROOT_DIR}/${DOCKERFILE_PATH}"
  --build-arg "MICROMAMBA_BASE_IMAGE=${LOCAL_MICROMAMBA_BASE_IMAGE}"
)

for name in \
  PIP_INDEX_URL \
  PIP_TRUSTED_HOST \
  HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY \
  http_proxy https_proxy all_proxy no_proxy; do
  value="${!name:-}"
  if [[ -n "${value}" ]]; then
    build_args+=(--build-arg "${name}=$(_rewrite_proxy_for_build_arg "${value}")")
  fi
done

echo "Building ${IMAGE_TAG} for ${DOCKER_PLATFORM}"
cd "${ROOT_DIR}"
docker buildx build "${build_args[@]}" .
