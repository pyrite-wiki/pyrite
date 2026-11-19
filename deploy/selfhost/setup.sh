#!/bin/bash
# Bootstrap a fresh Ubuntu 24.04 VPS for self-hosted Pyrite
# Run as root: curl -sL <raw-url> | bash
set -e

DOMAIN="${1:-}"

if [ -z "$DOMAIN" ]; then
    echo "Usage: bash setup.sh <your-domain>"
    echo "Example: bash setup.sh kb.example.com"
    exit 1
fi

echo "=== Installing Docker ==="
curl -fsSL https://get.docker.com | sh

echo "=== Creating deploy user ==="
useradd -m -s /bin/bash -G docker deploy

echo "=== Cloning repo and starting services ==="
su - deploy -c "
  git clone https://github.com/pyrite-wiki/pyrite.git ~/pyrite
  cd ~/pyrite
  PYRITE_DOMAIN=$DOMAIN docker compose -f deploy/selfhost/docker-compose.yml up -d --build

  echo 'Waiting for Pyrite to become healthy...'
  timeout 120 bash -c \"until docker compose -f deploy/selfhost/docker-compose.yml exec -T pyrite python -c \\\"import urllib.request; urllib.request.urlopen(\\\\\\\"http://localhost:8088/health\\\\\\\")\\\" 2>/dev/null; do sleep 5; done\"

  echo 'Seeding Pyrite KB...'
  docker compose -f deploy/selfhost/docker-compose.yml exec -T pyrite bash /app/deploy/selfhost/seed.sh
"

echo ""
echo "=== Setup complete ==="
echo ""
echo "1. Point $DOMAIN DNS (A record) to this server's IP"
echo "   Caddy will provision TLS automatically once DNS propagates."
echo ""
echo "2. Create your admin user:"
echo "   su - deploy"
echo "   cd ~/pyrite"
echo "   docker compose -f deploy/selfhost/docker-compose.yml exec pyrite pyrite-admin user create <username> --role admin"
echo ""
echo "3. Add more users later with the same command (--role read|write|admin)"
