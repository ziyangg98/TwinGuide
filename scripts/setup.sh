#!/bin/sh
set -eu

project_directory=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
site_packages="$project_directory/.blender-site-packages"
requirements="$project_directory/requirements-blender.lock.txt"
blender_python=${TWINGUIDE_BLENDER_PYTHON:-/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13}

if [ ! -x "$blender_python" ]; then
    echo "Blender 5.2 Python was not found: $blender_python" >&2
    exit 1
fi

temporary_site_packages=$(mktemp -d "$project_directory/.blender-site-packages.XXXXXX")
trap 'rm -rf "$temporary_site_packages"' EXIT
"$blender_python" -m pip install \
    --target "$temporary_site_packages" \
    --requirement "$requirements"

rm -rf "$site_packages"
mv "$temporary_site_packages" "$site_packages"
trap - EXIT

echo "Blender project dependencies installed at: $site_packages"
