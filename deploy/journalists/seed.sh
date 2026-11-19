#!/bin/bash
# Seed journalists.pyrite.wiki with investigative KBs
set -e

DATA_DIR="${PYRITE_DATA_DIR:-/data}"

# Skip if already seeded
if [ -f "$DATA_DIR/.seeded" ]; then
    echo "Already seeded, skipping."
    exit 0
fi

echo "=== Seeding journalists.pyrite.wiki ==="

# --- Copy KB data from seed volume ---
if [ -d /seed/kbs ]; then
    for kb_dir in /seed/kbs/*/; do
        kb_name=$(basename "$kb_dir")
        if [ ! -d "$DATA_DIR/$kb_name" ]; then
            cp -r "$kb_dir" "$DATA_DIR/$kb_name"
            echo "  Copied: $kb_name"
        fi
    done
fi

# --- Build config.yaml with all discovered KBs ---
echo "Building config.yaml..."
python3 -c "
from pathlib import Path
from pyrite.utils.yaml import load_yaml_file, dump_yaml_file

data_dir = Path('$DATA_DIR')
config_path = data_dir / 'config.yaml'

kbs = []
for d in sorted(data_dir.iterdir()):
    kb_yaml = d / 'kb.yaml'
    if d.is_dir() and kb_yaml.exists():
        meta = load_yaml_file(kb_yaml)
        name = meta.get('name', d.name)
        kb_type = meta.get('kb_type', meta.get('type', 'generic'))
        desc = meta.get('description', '')
        if isinstance(desc, str):
            desc = desc.strip()[:200]
        else:
            desc = ''
        kbs.append({
            'name': name,
            'path': str(d),
            'kb_type': kb_type,
            'description': desc,
            'read_only': True,
        })
        print(f'  Registered: {name} ({kb_type}) at {d}')

config = {
    'knowledge_bases': kbs,
    'settings': {
        'index_path': str(data_dir / 'index.db'),
        'host': '0.0.0.0',
        'port': 8088,
    },
}
dump_yaml_file(config, config_path)
print(f'Config written with {len(kbs)} KBs')
"

# --- Index all KBs ---
echo "=== Indexing all KBs ==="
pyrite index build --force --no-embed

# --- Build semantic embeddings ---
echo "=== Building embeddings (may take a few minutes) ==="
pyrite index embed 2>/dev/null || echo "  Embedding failed (non-fatal)"

touch "$DATA_DIR/.seeded"
echo ""
echo "=== Seed complete ==="
echo "Next steps:"
echo "  1. Create admin user: docker compose -f deploy/journalists/docker-compose.yml exec pyrite pyrite-admin user create <username> --role admin"
echo "  2. Create invite codes via the web UI (admin > settings)"
