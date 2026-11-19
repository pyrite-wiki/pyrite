#!/bin/bash
set -e

DATA_DIR="${PYRITE_DATA_DIR:-/data}"

# Skip if already seeded
if [ -f "$DATA_DIR/.seeded" ]; then
    echo "Already seeded, skipping."
    exit 0
fi

echo "Seeding demo KBs..."

# --- Copy Pyrite project KB ---
cp -r /seed/pyrite-kb "$DATA_DIR/pyrite-kb"
echo "  Copied: pyrite"

# --- Copy Demo KBs ---
if [ -d /seed/demo-kbs ]; then
    for kb_dir in /seed/demo-kbs/*/; do
        kb_name=$(basename "$kb_dir")
        if [ ! -f "$kb_dir/kb.yaml" ]; then
            echo "  Skipping $kb_name (no kb.yaml)"
            continue
        fi
        cp -r "$kb_dir" "$DATA_DIR/$kb_name"
        echo "  Copied: $kb_name"
    done
fi

# --- Build config.yaml with all discovered KBs ---
echo "Building config.yaml..."
CONFIG="$DATA_DIR/config.yaml"
cat > "$CONFIG" << 'HEADER'
knowledge_bases: []
settings:
  index_path: /data/index.db
HEADER

# Register each KB that has a kb.yaml. The demo is public, so each KB gets
# default_role: read (anonymous read) unless its kb.yaml says otherwise.
python3 -c "
import os, sys
sys.path.insert(0, '/app')
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
        kb_type = meta.get('kb_type', 'generic')
        desc = meta.get('description', '')
        if isinstance(desc, str):
            desc = desc.strip()[:200]
        else:
            desc = ''
        default_role = meta.get('default_role', 'read')
        kbs.append({
            'name': name,
            'path': str(d),
            'kb_type': kb_type,
            'description': desc,
            'default_role': default_role,
        })
        print(f'  Registered: {name} ({kb_type}, default_role={default_role}) at {d}')

config = {
    'knowledge_bases': kbs,
    'settings': {
        'index_path': str(data_dir / 'index.db'),
    },
}
dump_yaml_file(config, config_path)
print(f'Config written with {len(kbs)} KBs')
"

# --- Index all KBs ---
echo "Indexing all KBs..."
pyrite index build --force --no-embed

# --- Build embeddings ---
echo "Building semantic embeddings (this may take a few minutes)..."
pyrite index embed 2>/dev/null || echo "  Embedding failed (non-fatal)"

touch "$DATA_DIR/.seeded"
echo "Demo seeded successfully."
