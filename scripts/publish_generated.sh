#!/usr/bin/env bash
set -euo pipefail

source_dir="${1:?usage: publish_generated.sh SOURCE_DIR [REMOTE] [BRANCH]}"
remote="${2:-origin}"
branch="${3:-generated}"

for asset in cabinet-trace-dark.svg cabinet-trace-light.svg; do
  if [[ ! -f "$source_dir/$asset" ]]; then
    echo "missing generated asset: $source_dir/$asset" >&2
    exit 1
  fi
done

set +e
git ls-remote --exit-code --heads "$remote" "$branch" >/dev/null 2>&1
remote_status=$?
set -e

publish_root="$(mktemp -d)"
publish_dir="$publish_root/tree"
temporary_branch=""

cleanup() {
  if [[ -d "$publish_dir" ]]; then
    git worktree remove --force "$publish_dir" >/dev/null 2>&1 || true
  fi
  if [[ -n "$temporary_branch" ]]; then
    git branch -D "$temporary_branch" >/dev/null 2>&1 || true
  fi
  rm -rf "$publish_root"
}
trap cleanup EXIT

if [[ $remote_status -eq 0 ]]; then
  git fetch "$remote" "$branch:refs/remotes/$remote/$branch"
  git worktree add --detach "$publish_dir" "refs/remotes/$remote/$branch"
elif [[ $remote_status -eq 2 ]]; then
  git worktree add --detach "$publish_dir"
  temporary_branch="cabinet-publish-$$-$RANDOM"
  git -C "$publish_dir" switch --orphan "$temporary_branch"
  git -C "$publish_dir" rm -rf --ignore-unmatch .
else
  echo "could not inspect $remote/$branch" >&2
  exit "$remote_status"
fi

cp "$source_dir/cabinet-trace-dark.svg" "$publish_dir/cabinet-trace-dark.svg"
cp "$source_dir/cabinet-trace-light.svg" "$publish_dir/cabinet-trace-light.svg"

git -C "$publish_dir" config user.name "github-actions[bot]"
git -C "$publish_dir" config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git -C "$publish_dir" add cabinet-trace-dark.svg cabinet-trace-light.svg

if git -C "$publish_dir" diff --cached --quiet; then
  echo "Cabinet facts are unchanged."
  exit 0
fi

git -C "$publish_dir" commit -m "chore: refresh cabinet trace"
git -C "$publish_dir" push "$remote" "HEAD:$branch"
