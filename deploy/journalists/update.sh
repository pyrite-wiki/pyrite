#!/bin/bash
# Pull latest code and redeploy
# Run as the deploy user: bash ~/pyrite/deploy/journalists/update.sh
set -e

cd ~/pyrite
git pull
docker compose -f deploy/journalists/docker-compose.yml up -d --build
echo "Updated and restarted."
