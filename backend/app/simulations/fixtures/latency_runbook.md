# Latency breach diagnostics

Symptoms: LATENCY_BREACH, slow request paths, latency degradation.
The producer emits a breach event when a measured request exceeds its configured threshold.
Inspect request traces, query durations and dependency timing with read-only tools.
The available event sample does not establish a latency percentile or CPU utilization.
Consider slow dependencies, expensive queries and local resource contention as alternatives.
