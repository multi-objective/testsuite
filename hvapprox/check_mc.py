import math
import sys

reference = float(sys.argv[1])
tolerance = float(sys.argv[2])
estimate = float(sys.stdin.read())

if not math.isfinite(estimate) or abs(estimate - reference) > tolerance * abs(
    reference
):
    sys.exit(
        f"Monte Carlo estimate {estimate} is not within {tolerance:.1%} of {reference}"
    )

print("Monte Carlo estimate within tolerance")
