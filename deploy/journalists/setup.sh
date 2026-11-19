#!/bin/bash
# Setup journalists.pyrite.wiki on a fresh server
# Run as root: bash setup.sh
set -e

echo "=== Setting up journalists.pyrite.wiki ==="

# Install Docker if needed
if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi

# Create deploy user if needed
if ! id deploy &>/dev/null 2>&1; then
    echo "Creating deploy user..."
    useradd -m -s /bin/bash -G docker deploy
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps (as deploy user):"
echo "  1. git clone https://github.com/pyrite-wiki/pyrite.git ~/pyrite"
echo "  2. mkdir -p ~/pyrite/deploy/journalists/kbs"
echo "  3. Copy KB data into ~/pyrite/deploy/journalists/kbs/"
echo "  4. cd ~/pyrite && docker compose -f deploy/journalists/docker-compose.yml up -d --build"
echo "  5. Wait for health, then seed:"
echo "     docker compose -f deploy/journalists/docker-compose.yml exec pyrite bash /app/deploy/journalists/seed.sh"
echo "  6. Create admin: docker compose -f deploy/journalists/docker-compose.yml exec pyrite pyrite-admin user create <username> --role admin"
echo ""
echo "Point journalists.pyrite.wiki DNS to this server's IP."
