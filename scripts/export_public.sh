#!/usr/bin/env bash
# Export this private repository as a fresh public repository with a single commit.
#
# Usage: scripts/export_public.sh [--allow-dirty] <target-dir> [author-name] [author-email]
#
# - Copies HEAD (git archive) into <target-dir>; with --allow-dirty it copies the
#   working copy instead (tracked + untracked files not ignored by .gitignore), which
#   is meant only for testing the export before committing.
# - Removes private/ (internal docs), runs scripts/privacy_check.py on the copy and
#   refuses to continue if any forbidden personal string appears. Forbidden strings
#   are every non-noreply author e-mail in this repository's history plus the lines of
#   private/export_forbidden.txt (kept in the private repo so the list itself is
#   never published).
# - Creates a new git repository with one "Initial public release" commit authored by
#   the given identity. It never pushes: the next steps are printed instead.
set -euo pipefail

usage() {
  echo "usage: $0 [--allow-dirty] <target-dir> [author-name] [author-email]" >&2
  exit 2
}

allow_dirty=false
if [[ "${1:-}" == "--allow-dirty" ]]; then
  allow_dirty=true
  shift
fi
[[ $# -ge 1 && $# -le 3 ]] || usage

target="$1"
author_name="${2:-CRistin737}"
author_email="${3:-146782337+CRistin737@users.noreply.github.com}"

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

fail() {
  echo "export_public: $*" >&2
  exit 1
}

if [[ -e "$target" ]]; then
  [[ -d "$target" ]] || fail "target exists and is not a directory: $target"
  [[ -z "$(ls -A "$target")" ]] || fail "target directory is not empty: $target"
fi

if [[ "$allow_dirty" == false && -n "$(git status --porcelain)" ]]; then
  fail "working tree is dirty; commit or stash first (or use --allow-dirty to test)"
fi

forbidden_file="$repo_root/private/export_forbidden.txt"
[[ -f "$forbidden_file" ]] || fail "missing $forbidden_file (one forbidden string per line)"

mkdir -p "$target"
target="$(cd "$target" && pwd)"

echo "==> Copying files into $target"
if [[ "$allow_dirty" == true ]]; then
  # Tracked and untracked-but-not-ignored files that still exist on disk.
  git ls-files -z -co --exclude-standard --deduplicate \
    | while IFS= read -r -d '' path; do
        if [[ -e "$path" || -L "$path" ]]; then printf '%s\0' "$path"; fi
      done \
    | tar --null -T - -cf - \
    | tar -xf - -C "$target"
else
  git archive --format=tar HEAD | tar -xf - -C "$target"
fi

echo "==> Removing private/"
rm -rf "$target/private"

echo "==> Privacy check"
python3 "$repo_root/scripts/privacy_check.py" --root "$target" \
  || fail "privacy check reported findings in $target"

echo "==> Forbidden personal strings"
patterns="$(mktemp)"
trap 'rm -f "$patterns"' EXIT
{
  grep -v -e '^[[:space:]]*$' -e '^#' "$forbidden_file" || true
  git log --format='%ae%n%ce' | grep -v 'noreply' | sort -u || true
} | sed 's/[[:space:]]*$//' | sort -u > "$patterns"
[[ -s "$patterns" ]] || fail "no forbidden strings to check; refusing to continue"
if matches="$(grep -r -l -i -F -f "$patterns" "$target")"; then
  echo "$matches" >&2
  fail "forbidden personal strings found in the files above"
fi

echo "==> Creating the single public commit"
git -C "$target" init -q -b main
git -C "$target" add -A
git -C "$target" -c user.name="$author_name" -c user.email="$author_email" \
  -c commit.gpgsign=false commit -q -m "Initial public release"
git -C "$target" log --format='%h %an <%ae> %s'

cat <<EOF

Done. Nothing was pushed. Next steps:
  1. Create an empty public repository on GitHub (no README, license or .gitignore).
  2. cd "$target"
  3. git remote add origin https://github.com/CRistin737/hyverion-quant-ai.git
  4. git push -u origin main
EOF
