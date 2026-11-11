#!/bin/zsh
# Put the demo repository back to its starting state: a fresh shopkit with one commit.
source "${0:A:h}/_common.sh"
rm -rf "$DEMO_REPO" && mkdir -p "${DEMO_REPO:h}" && cp -R "$SHOPKIT" "$DEMO_REPO" && cd "$DEMO_REPO" \
  && git init -q && git config core.hooksPath .githooks && git add -A \
  && git -c user.name=dev -c user.email=dev@shopkit.test commit -q -m "chore: shopkit 0.4.0" >/dev/null 2>&1 \
  && echo "Fresh repo: $DEMO_REPO" && git log --oneline
