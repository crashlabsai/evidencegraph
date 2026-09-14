# Evidencegraph critical review — 2026-09-11

**Follow-up, 2026-09-14:** the earlier fix below stopped copies of public ledger
fields, but a new native-log probe copied the complete receipt, including its token,
from A into B and withheld A. The previous rule supported ten attributions with one
wrong event. Matching tokens now default to `ambiguous` / `receipt_possession_only`;
support requires explicit recorder authenticity or token exclusivity. An intentionally
false exclusivity declaration still reproduces the error. The
[stress protocol](sprint/protocol.md) and [work pack](sprint/README.md) record the
reproduction and the loss of correct attribution when exclusivity is unknown.
The historical review below describes the earlier revisions, not the current rule.

**Assessment: a promising evidence-audit workbench, with demonstrated correctness gaps and unproven investigator utility.** Keep developing its ability to preserve, compare, and explain evidence. Do not yet rely on its supported attribution or contradiction labels for an adversarial incident without reviewing the underlying observations.

This review covers the repository at `ded5e94`: the handoff, both dockets, architecture, acquisition, adapters, graph/storage lifecycle, reconciliation, identity/lineage, coverage, validation, Scout integration, CLI, export, and relevant tests. Three independent AI reviewers challenged forensic correctness, evaluation design, and product usefulness; these were not human investigator interviews. Their findings were consolidated with direct source inspection and new targeted probes.

The previously recorded 53-test run, demo, corpus reconstruction, and release checks were not rerun. Dependencies were installed from the lockfile to execute new counterexamples. Those counterexamples test different conditions from the release demonstration; they are not estimates of real-world failure rates. Production code and paid scanning configuration remain unchanged. No provider API credentials were configured and no paid scanner calls were made.

**What the project actually does well**

The central idea is useful: take multiple records of an incident, retain their original bytes, make their relationships inspectable, and state which questions remain unresolved. Snapshotting and hashes protect against accidental changes after acquisition. Native event and source-row citations help another investigator check a claim. Separating private scoring truth from investigator evidence avoids a major evaluation shortcut. Treating shared handles, network prefixes, and copied text cautiously is appropriate. The documentation is unusually explicit about important limitations.

The strongest present use case is a technical researcher auditing a published export or reconstructing a controlled agent experiment with known registry semantics. The wiki case demonstrates substantial data handling and reproducible descriptive checks. The lab demonstrates that the software can reconcile a small, instrumented scenario and identify fabricated receipts for nonexistent records. Neither establishes usefulness in a messy incident with incomplete or adversarial telemetry.

The intended beneficiary should be more specific than “agent incident forensics”: an investigator who already has native transcripts plus a separately collected artifact or process log, needs to resolve a small number of factual disputes, and must hand the reconstruction to another person. A general log viewer is a weaker reason to build this. Inspect already provides log exploration and portable viewing, and Scout already provides transcript scanning and validation workflows. The inference from those existing capabilities is that Evidencegraph's distinctive value must be cross-source verification and reviewable uncertainty. See the official [Inspect viewer documentation](https://inspect.aisi.org.uk/log-viewer.html) and [Scout workflow documentation](https://meridianlabs-ai.github.io/inspect_scout/workflow.html).

**Reproduced findings, ordered by consequence**

1. **A copied valid receipt can produce a confidently wrong attribution.** A targeted scenario drops A's authentic transcript, then appends to B's native log a fabricated write event whose arguments, receipt, and timestamp are copied from A's publicly visible registry record. No private truth is used to construct the forgery. The engine chooses B as supported. Private validation reports **10 supported attributions, 9 correct, precision 0.90, and one confident error**; the one injected spoof is missed. This is not a measured general error rate: it is one counterexample to the rule's adequacy under transcript forgery.

   The engine matches name, digest, receipt identifier, and timing, then promotes a unique candidate when trust domains are declared independent. A registry observation establishes that a mutation happened; a receipt copied into a transcript does not independently establish which transcript action caused it. The absence detector skips a claimed event ID when that ID exists in the ledger. See `src/evidencegraph/reconcile/engine.py:79`, `:132`, and `:189`, and `substrates/registry.py:56`.

   **Change needed:** state whether native runner logs are assumed authentic. If forged runner events are in scope, require an independently collected dispatch/process binding to support causation, or report a matching claim with attribution unresolved. Keep both nonexistent-receipt and copied-valid-receipt attacks in the evaluation. This connects directly to the missing Mac host observations.

2. **The absence check can falsely contradict a legitimate write.** With complete registry capture during seconds 0–10, an allegedly successful call spanning seconds 9–20 is marked contradicted when its receipt is absent. The actual mutation could have happened after capture stopped. The same check emits contradictions with no clock bound, or when the clock bound allows the operation to fall outside the capture window. It checks the operation's start only, ignoring completion and uncertainty (`reconcile/engine.py:206`).

   **Change needed:** require that the complete possible mutation interval lies within the independently covered interval. Otherwise abstain. A missing record is strong evidence only when collection was capable of observing it.

3. **Positive matching also ignores the operation's end.** A synchronous registry tool that finishes at second 1 can be selected as the producer of a mutation at second 86,400 with a one-second clock bound. Only late starts are excluded; a matching receipt can therefore outweigh an impossible execution window (`reconcile/engine.py:83`, `:113`, `:132`).

   **Change needed:** check overlap with the whole synchronous execution interval. Model background descendants separately rather than allowing completed synchronous calls to explain arbitrarily later mutations.

4. **Changed trust assumptions do not invalidate existing conclusions.** A new one-write case initially has a supported attribution under declared independent domains. Change `case.json` to declare those domains the same, render again, and export: the stale relation remains supported/independent, and bundle verification succeeds with `recomputed: true`. Explicitly rerunning reconciliation changes it to ambiguous/same.

   `case.py:170` checks the configuration fingerprint during ingestion, but `derive.py:62` and `docket/render.py:26` do not reject stale upstream stages. `export/bagit.py:184` recomputes a docket from existing graph rows, not the attribution from its evidence and current assumptions. That verification limit is documented, but the accepted inconsistency is still dangerous in a normal edit-and-render workflow.

   **Change needed:** bind derived results to their input/configuration fingerprints and reject or invalidate stale conclusions before rendering/export. This is a small correctness requirement for the existing workflow, not a reason to build a new workflow framework.

5. **LQ7 checks the wrong final event.** A B tool completes at second 1, the cache refresh occurs at second 10, and B has a native sandbox event at second 20. LQ7 still says refresh followed B's final event by nine seconds. It selects only tool events, despite asking about the last transcript event. Other event types are also discarded by the adapter (`docket/answers.py:345`; `adapters/inspect_eval.py:113`).

   **Change needed:** preserve and cite the actual endpoint named by the question. Either use the final relevant native event, with explicit semantics, or narrow the question to the last completed tool. Measuring clocks cannot fix selecting the wrong event.

6. **LQ8 reports a denial that was never observed.** A post-refresh cache read alone causes the headline to assert “protected route denied access,” even when no protected-route read or denial exists. A hash of arbitrary returned text also does not establish that the text was the protected fixture (`docket/answers.py:386`).

   **Change needed:** separate the observed cache read, observed denial, and protected-content identification. Each needs its own evidence and status. This is a direct example of the demonstration's story becoming hardcoded into a purported investigation answer.

7. **One matching sandbox event makes unrelated events look corroborative.** In a native log containing a matching `echo REAL` sandbox execution and an unrelated failed execution in the same time window, both become supported corroboration edges. The comparator calculates an aggregate result but returns all temporal candidates; ingestion applies that result to each pair. The edges cite only the tool event (`reconcile/consistency.py:139`; `adapters/inspect_eval.py:245`).

   **Change needed:** determine the result for each pair and cite both observations. “At least one match exists” does not mean every nearby event corroborates the tool.

8. **Capture-recapture can claim a lower bound larger than the true population.** The staged case has 12 real mutations, 11 claimed transcript writes including two fabricated claims, and nine matches. The calculation calls the union `12 + 11 - 9 = 14` a population lower bound, although only 12 real mutations exist. Dependence caveats do not fix counting nonexistent claims as population members (`coverage/estimate.py:45`; `coverage/capture_recapture.py:62`).

   **Change needed:** remove this population inference for unverified claims. Both shipped cases already violate its useful identification assumptions. Report observed counts, matching counts, disputed claims, and missing evidence. A new statistical subsystem is unnecessary.

9. **Legitimate empty logs cannot coexist as witnesses.** Adding `registry-reads.json` and `registry-refresh.json` when both contain `[]` fails because witness identity is based only on bytes while different filenames are rejected as conflicting semantics (`case.py:118`; `acquire.py:80`). Empty read and refresh logs are a natural incomplete-incident case.

   **Change needed:** distinguish the identity of stored bytes from the identity of an observation and its semantics. Reuse content storage without forcing different observations to share one witness record.

**Why the current reliability story is incomplete**

The release documents mostly describe their limits accurately. The risk is interpreting “precision 1.0” and “zero confident errors” as applying to the full docket or future incidents. Validation scores selected production joins, spoof flags, digest pairs, and read tuples. It does not score LQ2/LQ3/LQ7 as completed forensic answers, the denial statement in LQ8, or every contradiction/corroboration edge. The new examples show why that distinction matters.

Ten seeds vary fixture bytes and incidental values while retaining the same action structure, missing A transcript, and fabricated B receipts. They provide regression coverage, not ten independent incident types. The coverage interval is centered on an observed fraction that the staged validator compares to the same known scenario. Its inclusion of the true fraction is not evidence that uncertainty about missing witnesses or attribution is calibrated. In the receipt-replay failure, the interval still brackets truth while one supported attribution is wrong.

The `claimed-writes` scanner is explicitly asked to extract every claimed successful write, including uncorroborated claims. It is then scored against actual mutations. Correctly extracting a fabricated claim counts as a false positive on that different task. The mock supplies the answers programmatically. It is a useful control, but using the same setup with a paid LLM would not make it a fair forensic reliability comparison (`scanners/claimed_writes.py:20`; `scanners/validation.py:42`).

The status vocabulary also needs a clearer user contract. LQ1 says supported whenever reconciliation rows exist, even if all their outcomes are unmatched. That can mean “we support this summary of unresolved records,” but a reader can reasonably understand “write attribution is supported.” Similar broad statuses obscure which parts of multi-part questions were actually answered. Present answer completeness separately from the evidential strength of each claim.

**Pushback on the project's thesis and complexity**

- **The advantage may be instrumentation, not the graph.** Unique registry identifiers and independently retained records do much of the work in the successful lab. Compare against a small deterministic SQL join with the same evidence. If it gets the same answers and is easier to audit, keep Evidencegraph thin and focus on citations and handoff quality.
- **The two cases do not validate the same capability.** Wiki reconstruction tests descriptive auditing of a publisher's dependent export. The lab tests controlled attribution under privileged instrumentation. Success on one does not establish robustness on the other. Keep the shared evidence layer, but make case-specific claims and acceptance criteria explicit.
- **A fixed question list is good scope discipline, but can hide fixture-specific answers.** LQ2/LQ3 are still placeholders, and LQ8 imports an assumed denial. A question should be a contract with evidence requirements and failure cases, not merely a field that every report must fill.
- **The public/private split is methodologically useful, but can withhold the very evidence a real investigator needs.** Publish sufficient safe process/dispatch observations to answer causation while retaining a separate richer answer key. Renaming private ground truth as public evidence would invalidate the evaluation; collecting a separate usable observation is the needed work.
- **General-purpose infrastructure is premature.** Keep snapshots, citations, trust declarations, deterministic reconciliation, and a portable docket. Defer new graph vocabulary, more adapters, generalized agent runtimes, million-row optimization, and more population estimation until one investigator demonstrates a concrete need. Do not rebuild the viewer or scanner capabilities already available in Inspect/Scout.
- **Do not infer intent from these outputs.** Activity, byte equality, source claims, causal production, authenticated identity, and intent are different conclusions. The project's caveats acknowledge this; the prominent answer text should enforce it too.

**A useful LLM experiment, once credentials are configured**

The remaining issues for paid calls are access and experimental validity. The existing CLI still rejects live models. Avoid enabling a broad scanner merely to obtain a number. Start with a small isolated experiment on synthetic public evidence, retaining its exact inputs, outputs, model/configuration, token usage, and reported cost. Limit cases, retries, and output length; do not describe a client-side dollar estimate as an enforceable billing cap.

Use different tasks and keys for (a) extracting what a transcript claims and (b) determining which public evidence supports what actually happened. For the forensic task, compare a simple rules/SQL baseline, the current graph, an LLM with the same public evidence, and optionally an LLM reviewing the graph. Private truth is accessible only to grading. A transcript-only condition may be informative, but must be labeled as having less evidence.

Hold out scenario families, not merely seeds: copied receipts with the genuine transcript absent; multiple matching receipts; partial capture; missing or shifted clocks; delayed sandbox/model events; benign cache contents; and background descendants producing output after the initiating turn. Include ordinary successful incidents and ambiguous cases where abstention is the correct answer. Freeze questions and scoring before running the held-out set. Repeated model runs measure variability, not independent incident coverage.

Measure supported-attribution errors, false accusations, recovered correct facts, appropriate abstention, citation correctness, and errors per question. Report denominators and unscored questions. Check whether model explanations remain faithful to the evidence and whether embedded transcript instructions influence them. The useful outcome would be better candidate extraction or faster review without more unsupported conclusions; fluency alone is not an improvement.

**Recommended next decisions**

First, fix the reproduced attribution, time-window, stale-result, and false-answer problems and retain their counterexamples as regression cases. Clarify what “supported” guarantees. Remove the invalid population lower-bound claim.

Then complete the already identified Mac acceptance work: independently usable launch/dispatch observations, script-content identity at launch, process-to-output linkage, measured clock uncertainty tied to the actual run, and private validation of LQ2/LQ3/LQ7. This is the most useful next integration because it tests the project's missing causal evidence. It remains unfinished by this review.

In parallel with those fixes, recruit a real investigator to compare the docket against their existing log/SQL workflow on a previously unseen case. Suggested pilot criteria, to agree before the trial: no unsupported high-confidence accusations in the challenge set; every confident finding has a resolvable relevant citation; a second investigator can reproduce the key answers; and the tool saves enough investigation or handoff time to justify its setup. These are proposed acceptance criteria, not outcomes already measured.

If the simple baseline performs equally well and users do not benefit from the docket, reduce the project to a focused evidence-audit library. If investigators gain meaningful speed and checkability, invest in that workflow before widening the platform.

**Local review evidence**

The workspace's gitignored `.context/` directory retains executable probes and independent review notes:

- `forensic-review.md` and `forensic_repro.py`: temporal, LQ7/LQ8, and sandbox-citation counterexamples.
- `validation-review.md`, `reproduce_receipt_replay.py`, and `receipt-replay-90cae17b/result.json`: copied-valid-receipt attack and private scores.
- `product-review.md`, `review_docket_probe.py`, and `review_witness_probe.py`: docket assertions and identical empty-log collision.
- `review/config_probe.py` and `review/config_probe.json`: stale trust configuration accepted through render/export verification.

These scratch files are local review evidence and will not accompany a commit of this document. Convert the relevant probes into maintained regression tests when implementing the fixes. The findings, triggers, observed results, and source locations above are retained in this report so its conclusions do not depend on those temporary files.

**Resolution — 2026-09-11**

Each reproduced finding above was fixed on this branch and its probe converted into a maintained regression test. Nothing below re-measures general reliability; it records what the code now does on the counterexamples.

1. Copied receipt. `TrustDomain.authentic_records` states whether a domain's records could have been fabricated by the investigated actors. A unique matching receipt is supported only when the registry's published `receipt_token_sha256` commitment binds the transcript's `receipt_token`, or when the transcript domain is declared authentic (the docket then names that assumption); otherwise it is ambiguous, and a contradicting token is contradicted. Matched claims that fail the binding are reported as `executed` relations for LQ5. The lab registry now issues tokens and publishes their hashes. On the replay scenario the forged claim is ambiguous with zero confident errors (`tests/test_lab_stage.py::test_copied_public_receipt_is_not_attributed`). Not defended: an actor relaying a genuine receipt.
2. Absence check. Contradiction requires the whole tool interval, widened by a declared bound on both sides, inside the declared capture window; otherwise not assessable. The stage settles 1.1 s at each end so its declared window honours the 1 s bound (`tests/test_engine.py::test_absence_needs_the_whole_widened_tool_window_inside_capture`).
3. Execution window. A candidate that completed before the record minus the bound is excluded; all-excluded candidates are `contradicted` with method `execution_window_disjoint`. Listing-style records with unknown start skip that exclusion, which is where the opt-in 2B rule now applies (`tests/test_engine.py::test_completed_call_cannot_produce_a_later_mutation`).
4. Stale assumptions. Every ingested and derived stage, validation and docket records the `case.json` hash; reconcile, coverage, validate, render and export raise when any stage is stale (`tests/test_case_lifecycle.py::test_changed_trust_assumptions_invalidate_derived_results`).
5. LQ7. The adapter emits a cited `transcript_final_event` entity over every native event type; LQ7 compares against it and reports the event type (`tests/test_docket.py::test_lq7_uses_the_final_recorded_transcript_event`).
6. LQ8. Post-refresh cache reads, the refresh log's payload commitment, matched reads and observed denials are separate parts; no denial is asserted unless recorded, and without a commitment the served bytes are not identified. The validator scores fixture exposure against private truth (`tests/test_docket.py::test_lq8_reports_only_observed_parts`).
7. Sandbox pairs. Each tool/sandbox pair gets its own outcome (`unmatched` for a different command) and cites both events (`tests/test_inspect_eval.py::test_unrelated_sandbox_execution_is_not_corroboration`).
8. Capture-recapture. Removed for the registry case; LQ6 reports observed counts. The remaining Chapman interpretation no longer calls the union a lower bound unless membership and linkage are verified (`tests/test_capture_recapture.py`).
9. Witness identity. A witness id hashes content with adapter and filename, so two empty logs coexist; identical bytes may never be declared in two trust domains (`tests/test_case_lifecycle.py::test_identical_empty_logs_are_separate_observations`).

Also changed: LQ1 takes the status of its evidence, never supported for a list of unresolved records. Still open, as recommended above: the Mac collection with public launch/process observations and a measured clock bound, a matched LLM comparison, and a named investigator's trial.
