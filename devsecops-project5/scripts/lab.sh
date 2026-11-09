#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
CLUSTER=project5-lab
NODE_IMAGE=kindest/node@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f
POSTGRES_IMAGE=postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94
CALICO_SHA=2de8f47595fb9c41b3f47d7b767a1f8e72ecf84057af834738ff12689a234da5
export KUBECONFIG="$ROOT/.lab/kubeconfig"
mkdir -p .lab/bin .lab/cache
KUBECTL="$ROOT/.lab/bin/kubectl"

client() {
  # Use a kubectl matching the lab server instead of the host's older client.
  if [[ ! -x "$KUBECTL" ]]; then
    docker cp "$CLUSTER-control-plane:/usr/bin/kubectl" "$KUBECTL"
    chmod +x "$KUBECTL"
  fi
}
k() { "$KUBECTL" --kubeconfig "$KUBECONFIG" --context "kind-$CLUSTER" "$@"; }
guard() {
  client
  [[ "$(docker inspect "$CLUSTER-control-plane" --format '{{ index .Config.Labels "io.x-k8s.kind.cluster" }}')" == "$CLUSTER" ]]
  [[ "$(k config view --minify -o jsonpath='{.clusters[0].cluster.server}')" == https://127.0.0.1:* ]]
  [[ "$(k get node "$CLUSTER-control-plane" -o jsonpath='{.metadata.labels.project5-lab}')" == true ]]
}
cluster() {
  if ! kind get clusters | grep -Fxq "$CLUSTER"; then
    # Nodes cannot become Ready before the replacement CNI is installed.
    kind create cluster --name "$CLUSTER" --image "$NODE_IMAGE" --config k8s/kind.yaml --kubeconfig "$KUBECONFIG" --retain --wait 0s
  elif [[ ! -s "$KUBECONFIG" ]]; then
    kind get kubeconfig --name "$CLUSTER" > "$KUBECONFIG"
  fi
  chmod 600 "$KUBECONFIG"
  guard
  if [[ ! -f .lab/cache/calico-v3.33.0.yaml ]]; then
    curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 \
      https://raw.githubusercontent.com/projectcalico/calico/v3.33.0/manifests/calico.yaml \
      -o .lab/cache/calico-v3.33.0.yaml
  fi
  printf '%s  %s\n' "$CALICO_SHA" .lab/cache/calico-v3.33.0.yaml | sha256sum -c -
  # Server-side apply avoids the size limit of last-applied CRD annotations.
  k apply --server-side -f .lab/cache/calico-v3.33.0.yaml
  k -n kube-system rollout status daemonset/calico-node --timeout=900s
  k -n kube-system rollout status deployment/calico-kube-controllers --timeout=300s
  k wait --for=condition=Ready node --all --timeout=180s
  k -n kube-system rollout status deployment/coredns --timeout=180s
}
build() {
  guard
  docker build -t project5-backend:phase1 app/backend
  docker build -t project5-frontend:phase1 app/frontend
  docker pull "$POSTGRES_IMAGE"
  # Docker archive loading does not retain upstream multi-architecture digests.
  # Give the digest-selected image a local alias and prohibit registry pulls.
  docker tag "$POSTGRES_IMAGE" project5-postgres:17.6
  kind load docker-image --name "$CLUSTER" project5-backend:phase1 project5-frontend:phase1 project5-postgres:17.6
}
deploy() {
  guard
  k apply -k k8s/base
  for workload in postgres backend frontend; do
    k -n project5 rollout status "deployment/$workload" --timeout=240s
  done
}
case "${1:-help}" in
  up) cluster; build; deploy ;;
  cluster) cluster ;;
  build) build ;;
  deploy) deploy ;;
  forward) guard; exec "$KUBECTL" --kubeconfig "$KUBECONFIG" --context "kind-$CLUSTER" -n project5 port-forward --address 127.0.0.1 service/frontend 8080:8080 ;;
  status) guard; k get nodes; k -n project5 get deployments,pods,services,pvc; k get storageclasses ;;
  kubectl) shift; guard; k "$@" ;;
  *) echo 'Usage: bash scripts/lab.sh {up|cluster|build|deploy|forward|status|kubectl ...}' ;;
esac
