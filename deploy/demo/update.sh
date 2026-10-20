#!/bin/bash
# Pull latest release and redeploy the demo site.
# Run as the deploy user: bash ~/pyrite/deploy/demo/update.sh [tag]
# If no tag is given, pulls the latest from main.
set -e

TAG="${1:-}"

cd ~/pyrite
git fetch --tags
if [ -n "$TAG" ]; then
    echo "Deploying tag: $TAG"
    git checkout "$TAG"
else
    echo "Deploying latest main"
    git checkout main
    git pull
fi

cd ~/pyrite-kb-demo
git pull

cd ~/pyrite
docker compose -f deploy/demo/docker-compose.yml up -d --build

echo "Updated and restarted."
docker compose -f deploy/demo/docker-compose.yml ps
docker compose -f deploy/demo/docker-compose.yml exec pyrite pyrite --version 2>/dev/null || true
