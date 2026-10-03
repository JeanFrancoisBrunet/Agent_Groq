#!/bin/bash
 
cd "$(dirname "$0")" || exit 1
 
git add .
 
if git diff --cached --quiet; then
echo "Aucune modification"
exit 0
fi
 
git commit -m "${1:-mise à jour}"
git push
 
echo "✅ Synchronisation terminée"
