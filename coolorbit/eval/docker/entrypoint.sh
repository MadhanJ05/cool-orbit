#!/bin/sh
# Container entrypoint: a writable throwaway HOME, the ALCHEMI skills baked into the image, then the command.
set -e
mkdir -p "$HOME"
if [ ! -d /work/.claude/skills ]; then
  mkdir -p /work/.claude
  cp -R /opt/alchemi-skills /work/.claude/skills
fi
exec "$@"
