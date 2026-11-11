# Copy-only validation rewrite candidate

This branch adds an opt-in structural-validation rewrite. It does not activate a trial, extend a paid approval, or change production workflows.

- A newly prepared plan must include `max_validation_rewrites=1` and `max_submissions=2`. Changing a profile after approval invalidates the plan.
- The first known structural failure is settled and archived. Its field, validation code, reason and redacted model response are supplied to one rewrite using the same immutable source input and full validator.
- The rewrite gets a distinct durable request ID, reservation and usage receipt. Restart and duplicate worker delivery cannot create a third submission. Only the final sample result counts toward consecutive validation failures.
- Identity, binding, execution approval TTL, trial expiry, copy-only scope, request and cost limits are rechecked before the rewrite. `needs_evidence`, network/HTTP errors, unknown outcomes and truncated responses are not automatically rewritten.
- Oversized repair context is reported, not silently truncated. No factual constraints are loosened to make a sample pass.

## Content preparation

The last shot is normalized to `cta_product`. Non-final generated-hair visualization cannot contain packaging even if a different evidence label is supplied. A packaging CTA must use a closed carton, either held by a front-facing speaking presenter or shown alone static/rotating with voiceover. Existing no-opening and no-application constraints remain.

`prepare_policy_batch` compiles context-aware soft/direct CTA preferences into fresh hashed input snapshots, balancing choices against recent endings. It rejects already compiled input instead of stacking incompatible rules. Existing approved inputs remain immutable. This is a preference mechanism; output repetition must also be measured during the trial.

The presentation predicate allows back-view/hair-detail/product-only voiceover without a visible mouth. Front-facing speaking and global no-mouth instructions remain conflicts. Project-specific validation must import this predicate while retaining its overall front-camera speech requirement.

New packaging/shade claims remain a separate human-approval gate. These text checks supplement the full validator and human review; they do not certify visual correctness or factual truth.

## Evidence and rollout

Cloud CI uses synthetic providers and a network-isolated runtime. Tests cover bounded rewrite, settlement, interruption, duplicate execution, pending outcomes, continuation, CTA packaging and presentation rules. A successful synthetic check is not a measured real-model success rate. Deployment and any multi-day real-task schedule require their own approved contract and readback.
