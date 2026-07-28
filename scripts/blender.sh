#!/bin/sh
set -eu

project_directory=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
blender_binary="/Applications/Blender.app/Contents/MacOS/Blender"
site_packages="$project_directory/.blender-site-packages"

if [ ! -x "$blender_binary" ]; then
    echo "Blender 5.2 was not found: $blender_binary" >&2
    exit 1
fi
if [ ! -d "$site_packages" ]; then
    echo "Project dependencies are missing; run scripts/setup.sh first." >&2
    exit 1
fi

export PYTHONPATH="$site_packages:$project_directory/src:$project_directory"
exec "$blender_binary" \
    --python-use-system-env \
    --python-exit-code 1 \
    "$@"
