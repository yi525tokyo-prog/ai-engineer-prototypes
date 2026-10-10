#!/bin/sh
# Wrap an Artifact-style page (no <head>) into a standalone index.html.
# usage: games/wrap.sh games/<name>
d="$1"
{ printf '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
  sed -n '1,/<\/style>/p' "$d/game.html"
  printf '</head>\n<body>\n'
  sed -n '/<\/style>/,$p' "$d/game.html" | tail -n +2
  printf '</body>\n</html>\n'; } > "$d/index.html"
