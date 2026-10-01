#!/bin/bash
# Verified release entry for Ubuntu 24.04 x86_64. Never pipe downloaded code to sh.
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
fail() { echo "{\"error\":\"$1\",\"business_ready\":false}"; exit 2; }
mode=${1:---check}
[[ "$mode" == --check || "$mode" == --apply ]] || fail BOOTSTRAP_MODE_INVALID
shift || true
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 && "$EUID" == 0 ]] || fail UBUNTU_2404_X86_64_ROOT_REQUIRED
# Read values as data, never source an arbitrary shell file.
os_id=$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')
os_version=$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')
[[ "$os_id" == ubuntu && "$os_version" == 24.04 ]] || fail UBUNTU_2404_REQUIRED
[[ -z "${DOCKER_HOST:-}" || "$DOCKER_HOST" == unix:///var/run/docker.sock ]] || fail LOCAL_DOCKER_REQUIRED
[[ -z "${DOCKER_CONTEXT:-}" || "$DOCKER_CONTEXT" == default ]] || fail LOCAL_DOCKER_REQUIRED
missing=()
for package in python3.12 python3.12-venv ca-certificates; do
  [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" == 'install ok installed' ]] || missing+=("$package")
done
if command -v docker >/dev/null; then
  docker compose version --short >/dev/null 2>&1 || fail EXISTING_DOCKER_COMPOSE_REQUIRED_NO_ENGINE_REPLACEMENT
else
  # An existing non-Ubuntu engine/CLI or containerd must be reconciled, not removed.
  for package in docker-ce docker-ce-cli containerd.io podman-docker; do
    [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != 'install ok installed' ]] || fail EXISTING_ENGINE_REQUIRES_RECONCILIATION
  done
  missing+=(docker.io docker-compose-v2)
fi
if [[ "$mode" == --check ]]; then
  printf '{"status":"host_plan","os":"ubuntu24.04","packages":"%s","services_started":false,"business_ready":false}\n' "${missing[*]}"
  exit 0
fi
[[ -d /run/systemd/system ]] || fail SYSTEMD_HOST_REQUIRED
# Do not run full upgrade, remove an existing engine, or modify firewall rules.
if ((${#missing[@]})); then
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o DPkg::Lock::Timeout=120 update -qq
  apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends "${missing[@]}"
fi
systemctl enable --now docker
python3.12 -I -c 'import ensurepip,sys; assert sys.version_info[:2] == (3,12)'
[[ "$(docker context inspect --format '{{.Endpoints.docker.Host}}')" == unix:///var/run/docker.sock ]] || fail LOCAL_DOCKER_REQUIRED
compose=$(docker compose version --short)
python3.12 -I -c 'import re,sys; m=re.fullmatch(r"v?(\d+)\.(\d+)\..*",sys.argv[1]); assert m and tuple(map(int,m.groups())) >= (2,24)' "$compose" || fail COMPOSE_224_REQUIRED
docker info --format '{{.OSType}}' | /usr/bin/grep -qx linux || fail DOCKER_NOT_READY
printf '{"status":"host_dependencies_ready","services_started":false,"business_ready":false}\n'
if (($#)); then
  bundle=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
  exec python3.12 -I "$bundle/start.py" "$@"
fi
