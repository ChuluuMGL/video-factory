#!/bin/bash
# Real empty Ubuntu guest. Downloads and package installs remain on cloud CI.
set -euo pipefail
mkdir -p ci-host
cd ci-host
curl --fail --location --retry 2 -o SHA256SUMS https://cloud-images.ubuntu.com/minimal/releases/noble/release/SHA256SUMS
image=ubuntu-24.04-minimal-cloudimg-amd64.img
curl --fail --location --retry 2 -o "$image" "https://cloud-images.ubuntu.com/minimal/releases/noble/release/$image"
awk -v name="$image" '$2==name || $2=="*"name {print}' SHA256SUMS > selected.sha256
test "$(wc -l < selected.sha256)" -eq 1
sha256sum --check selected.sha256
qemu-img resize "$image" 14G
ssh-keygen -t ed25519 -N '' -f guest-key -q
cat > user-data <<EOF
#cloud-config
users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat guest-key.pub)
ssh_pwauth: false
disable_root: true
EOF
printf 'instance-id: vf-bootstrap-ci\nlocal-hostname: vf-bootstrap-ci\n' > meta-data
cloud-localds seed.img user-data meta-data
# Use nested KVM when available; TCG keeps the test possible on standard runners.
accel=tcg
if [[ -r /dev/kvm && -w /dev/kvm ]]; then accel=kvm; fi
cp /usr/share/OVMF/OVMF_VARS_4M.fd guest-vars.fd
qemu-system-x86_64 -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd -drive if=pflash,format=raw,file=guest-vars.fd -accel "$accel" -m 4096 -smp 2 -display none -daemonize -pidfile guest.pid \
 -drive "file=$image,format=qcow2,if=virtio" -drive file=seed.img,format=raw,if=virtio \
 -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22 -serial file:guest-serial.log
trap 'kill "$(cat guest.pid)" 2>/dev/null || true; rm -f guest-key guest-key.pub user-data' EXIT
# This generated local disposable guest has no previously known host fingerprint.
# Record its first key only in its dedicated known_hosts; never use this for customers.
ssh_args=(-i guest-key -p 2222 -o ConnectTimeout=3 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=guest-known-hosts -o BatchMode=yes)
ready=0
for i in $(seq 1 90); do
 if timeout 180 ssh "${ssh_args[@]}" ubuntu@127.0.0.1 'cloud-init status --wait' > cloud-init.log 2>/dev/null; then ready=1; break; fi
 sleep 5
done
test "$ready" -eq 1
scp -q -i guest-key -P 2222 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=guest-known-hosts ../distribution/bootstrap.sh ubuntu@127.0.0.1:bootstrap.sh
ssh "${ssh_args[@]}" ubuntu@127.0.0.1 'sudo bash bootstrap.sh --check' > before.json
ssh "${ssh_args[@]}" ubuntu@127.0.0.1 'sudo bash bootstrap.sh --apply' > install.log
ssh "${ssh_args[@]}" ubuntu@127.0.0.1 'sudo bash bootstrap.sh --check' > after.json
ssh "${ssh_args[@]}" ubuntu@127.0.0.1 'sudo bash bootstrap.sh --apply' > repeat.log
python3 - <<'PY'
import json,pathlib
before=json.loads(pathlib.Path('before.json').read_text())
after=json.loads(pathlib.Path('after.json').read_text())
assert 'docker.io' in before['packages'] and after['packages']==''
assert 'host_dependencies_ready' in pathlib.Path('install.log').read_text()
assert 'host_dependencies_ready' in pathlib.Path('repeat.log').read_text()
pathlib.Path('result.json').write_text(json.dumps({'status':'PASS','environment':'fresh_ubuntu_2404_vm','host_dependencies':'installed_and_reused','docker_daemon':'verified','product_services':'not_started','paid_model_requests':0}))
PY
