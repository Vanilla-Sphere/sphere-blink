

import os as _os

# One numeric thread per process. Inside a container the maths libraries see
# every core of the host and start that many threads, which then fight over
# the few CPUs the container may actually use. Parallelism comes from the
# worker processes instead (see worker.py). Must run before numpy is imported.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    _os.environ.setdefault(_var, "1")
