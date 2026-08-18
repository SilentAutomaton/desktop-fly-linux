"""Upstream's two acceptance suites, ported number for number.

Both are stochastic end to end, so they seed Python's `random` and the network
from one fixed value unless the caller asks otherwise. That keeps them a
regression gate; `--seed N` turns them back into an exploration of how robust
the operating point is.

Measured over seeds 1..20, 16 pass every check. The two that can fail are
upstream thresholds that are simply tight against upstream's own randomness:
"ledge attach" gives the fly four seconds of random-walk heading to drift into
a window edge (3 seeds in 20), and "thermal tempo" wants a 10 px/s speed gap to
open inside two seconds (1 in 20). Neither is a port defect, and neither is
loosened here - the thresholds are upstream's and they stay upstream's.
"""

# The FlyWire release these suites were written against.
DEFAULT_SEED = 783
