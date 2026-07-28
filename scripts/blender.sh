#!/bin/sh
set -eu

project_directory=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
site_packages="$project_directory/.blender-site-packages"

if [ -n "${TWINGUIDE_BLENDER:-}" ]; then
    blender_binary=$TWINGUIDE_BLENDER
elif command -v blender >/dev/null 2>&1; then
    blender_binary=$(command -v blender)
else
    blender_binary="/Applications/Blender.app/Contents/MacOS/Blender"
fi

if [ ! -x "$blender_binary" ]; then
    echo "Blender was not found: $blender_binary" >&2
    exit 1
fi
if [ ! -d "$site_packages" ]; then
    echo "Project dependencies are missing; run scripts/setup.sh first." >&2
    exit 1
fi
case $("$blender_binary" --version | sed -n '1p') in
    "Blender 5.2."*) ;;
    *)
        echo "TwinGuide requires Blender 5.2." >&2
        exit 1
        ;;
esac

export PYTHONPATH="$site_packages:$project_directory/src:$project_directory"
exec "$blender_binary" \
    --python-use-system-env \
    --python-exit-code 1 \
    "$@"
