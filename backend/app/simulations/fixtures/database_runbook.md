# Database connection pool troubleshooting

Symptoms: DB_CONNECTION_TIMEOUT, database connection timeout, pool saturation.

Inspect active connections and configured pool sizing using approved read-only views.
Compare connection usage with recent request traffic and inspect potential connection leaks.
Review dependency health and connection acquisition timing before choosing a causal explanation.
Timeouts can also result from network interruptions or an unavailable dependency.
These are diagnostic suggestions; no root cause is established by the error code alone.
Configuration changes and production interventions require a separate human review.
