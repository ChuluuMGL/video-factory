# Copy-only script trial recovery

Every provider response is observed before decoding. The durable `script:receipt` journal contains HTTP status, elapsed milliseconds, bounded usage, finish/truncation indicators, response size and SHA256, and allowlisted validation reasons. It contains no headers, credentials, body text, arbitrary upstream error text, or business prompts. Validation observations append to the transport observation; a decoder failure cannot erase it.

Errors distinguish network failures, model length truncation, invalid JSON/envelopes, and structured validation failures. Each POST is attempted once. These errors preserve the unknown submission intent and budget reservation; a response receipt is not proof of billing reconciliation or human approval.

An administrator may recover a paused trial only with a new recorded user instruction, the exact current-state fingerprint, explicitly named uncertain requests, and a new checkpoint task. The new 24-hour window is recorded alongside prior windows. Quarantined requests remain uncertain, retain their reservation, consume the request cap, and cannot be resubmitted or duplicated under a different task name. A new unknown request blocks further dispatch. This recovery does not extend per-plan approval TTLs or grant model execution authority.

After recovery, register the new checkpoint in the copy-only record map, arm the new deadline, and verify it against the actual durable window. The first recovered draft requires genuine review before further dispatch. Identity is checked for actual approval/rejection transitions; no-op status events are ignored, not interpreted as denied reviewers. Never assign roles based only on an event ID or a cell value.

Before recovery, inspect the provider account's usage evidence and deployed egress configuration without issuing a generation request. Preserve an inconclusive original response honestly. Do not reconstruct or invent its receipt.
