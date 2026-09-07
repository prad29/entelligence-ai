#!/bin/bash
set -e

cd /app

# Stop and remove existing containers gracefully.
# Ignore error if no containers are running (first deploy).
docker compose -f docker-compose.prod.yml down --remove-orphans || true

# Free root-volume disk space before the next deploy pulls new images.
# Each deploy tags its backend/frontend image with a distinct IMAGE_TAG
# (see start_containers.sh), so the existing `docker image prune -f` there
# (no -a) only ever catches dangling/untagged layers -- every previous
# deploy's still-tagged image stays on disk forever, which is what filled
# the disk (confirmed: "no space left on device" while extracting a layer
# under /var/lib/containerd/...). `-a` removes every image not in use by a
# container, tagged or not, now that `down` above has stopped them all.
#
# Deliberately NOT running `docker volume prune` here: vespa_data is
# bind-mounted to its own dedicated EBS volume (/data/vespa), not the root
# volume that actually filled up, so pruning volumes wouldn't free any of
# the space that caused this failure -- it would only risk deleting the
# semantic search index (a ~10 hour rebuild) for no benefit.
docker container prune -f || true
docker image prune -af || true
docker builder prune -af || true

echo "Disk usage after cleanup:"
df -h / || true
