#!/bin/bash

set -e

PROG="../bin/hv"
ORIG="./hv/bug-dtlz-linear-8d.inp"
WORK="current.txt"

cp "$ORIG" "$WORK"

# Function to test whether the bug still occurs
bug_triggers() {
    local value
    value=$($PROG -r '1 1 1 1 1 1 1 1' < "$1")
    awk "BEGIN { exit !($value >= 1.0) }"
}

# Sanity check
if ! bug_triggers "$WORK"; then
    echo "ERROR: Original input does not trigger the bug."
    exit 1
fi

echo "Bug confirmed. Starting minimization..."

lines=$(wc -l < "$WORK")
chunk_size=$((lines / 2))

while [ "$chunk_size" -ge 1 ]; do
    echo "Trying chunk size: $chunk_size"
    reduced=false

    total_lines=$(wc -l < "$WORK")
    start=1

    while [ "$start" -le "$total_lines" ]; do
        end=$((start + chunk_size - 1))

        # Create candidate by removing [start, end]
        awk -v s="$start" -v e="$end" 'NR < s || NR > e' "$WORK" > candidate.txt

        candidate_lines=$(wc -l < candidate.txt)

        if [ "$candidate_lines" -ge 2 ]; then
            if bug_triggers candidate.txt; then
                echo "  Removed lines $start-$end (now $candidate_lines lines)"
                mv candidate.txt "$WORK"
                reduced=true
                break
            fi
        fi

        start=$((start + chunk_size))
    done

    if ! $reduced; then
        chunk_size=$((chunk_size / 2))
    fi
done

echo "Minimization complete."
echo "Final testcase:"
wc -l "$WORK"
cat "$WORK"
