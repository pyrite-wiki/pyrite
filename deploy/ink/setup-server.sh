#!/bin/bash
# Bootstrap script for a fresh VPS running Ubuntu 24.04
# Run as root: bash setup-server.sh
set -e

echo "=== Installing Docker ==="
curl -fsSL https://get.docker.com | sh

echo "=== Creating deploy user ==="
useradd -m -s /bin/bash -G docker deploy

echo "=== Cloning repo and starting services ==="
su - deploy -c '
  git clone https://github.com/pyrite-wiki/pyrite.git ~/pyrite
  cd ~/pyrite
  docker compose -f deploy/ink/docker-compose.yml up -d --build

  echo "Waiting for Pyrite to become healthy..."
  timeout 120 bash -c "until docker compose -f deploy/ink/docker-compose.yml exec -T pyrite python -c \"import urllib.request; urllib.request.urlopen(\\\"http://localhost:8088/health\\\")\" 2>/dev/null; do sleep 5; done"

  echo "Seeding Pyrite KB..."
  docker compose -f deploy/ink/docker-compose.yml exec -T pyrite bash /app/deploy/ink/seed.sh
'

echo ""
echo "=== Setup complete ==="
echo "Point pyrite.ink DNS (A record) to this server's IP."
echo "Caddy will provision TLS automatically once DNS propagates."
echo ""
echo "Create your admin user:"
echo "  su - deploy"
echo "  cd ~/pyrite"
echo "  docker compose -f deploy/ink/docker-compose.yml exec pyrite pyrite-admin user create <username> --role admin"
echo ""
echo "Add more users later with:"
echo "  docker compose -f deploy/ink/docker-compose.yml exec pyrite pyrite-admin user create <username> --role read"
echo "  (--role read|write|admin)"
