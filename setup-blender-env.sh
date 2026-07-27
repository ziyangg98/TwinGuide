#!/bin/sh
set -eu

project_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
blender_python="/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13"
site_packages="$project_directory/.blender-site-packages"
requirements="$project_directory/requirements-blender.lock.txt"

if [ ! -x "$blender_python" ]; then
    echo "Blender 5.2 Python was not found: $blender_python" >&2
    exit 1
fi

"$blender_python" -m pip install \
    --upgrade \
    --target "$site_packages" \
    --requirement "$requirements"

echo "Blender project dependencies installed in: $site_packages"
