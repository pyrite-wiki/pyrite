#!/bin/bash
set -e

DATA_DIR="${PYRITE_DATA_DIR:-/data}"

# Skip if already seeded
if [ -f "$DATA_DIR/.seeded" ]; then
    echo "Already seeded, skipping."
    exit 0
fi

echo "Seeding Pyrite KB..."

# Copy the bundled KB into the data directory
cp -r /seed/pyrite-kb "$DATA_DIR/pyrite-kb"

# Register and index the KB
pyrite kb add "$DATA_DIR/pyrite-kb" --name pyrite
pyrite index build -k pyrite --force
pyrite index embed -k pyrite

touch "$DATA_DIR/.seeded"
echo "Pyrite KB seeded successfully."
