# Evidence requests before attributing an agent action

This is a collection aid for the registry/native-transcript workflow, not a
containment certification or a legal reporting instrument. Request the cheap,
existing records first. Installation and investigator-time costs have not been
measured. Each sufficient answer is conditional on the stated collection scope.

| Question to the collector | Evidence that would permit the inference | What does not settle it | Executable check |
|---|---|---|---|
| What bytes were acquired, and when? | Original files, source location, acquisition timestamp, retained root digest and custody record | A screenshot of a successful command; a hash supplied only alongside editable content | `eg cite` re-hashes the exact fragment; bundle verification checks inventory integrity |
| Did this mutation occur in the declared namespace? | A separately collected registry row with event id, namespace, payload digest and mutation time | A transcript receipt alone; a copy of that receipt in a second file | `fabricated`, `copied_public_receipt` |
| Could an actor create or edit native tool events? | Recorder architecture, access policy and collection audit showing events were recorded outside investigated actors' control, including request/result association | Calling a file “native”; hashing it after the incident; two logs from the same compromised runner | Declare `authentic_records` only if the collection audit justifies it |
| Could this receipt have been copied into another event? | Evidence of exclusive token delivery and retention, or independently recorded dispatch-to-mutation association | The correct token; the same token hash; a token obtained from a shared transcript | Compare `full_receipt_relay` and `false_exclusivity`; leave exclusivity unset when unknown |
| Are the two observations independent? | Separate capture paths and a documented analysis of shared failure or editing control | Separate filenames, storage buckets or services with one writable upstream source | `dependent_witnesses` |
| What clock error is possible? | Recorded call start/end, mutation interval, clock-offset measurement with its scope and uncertainty | Assuming timestamps use the same clock because both end in `Z` | `missing_clock_bound`; inspect the full interval before selecting a candidate |
| Was a claimed missing write observable throughout? | Complete accepted-mutation enumeration for the namespace; collection start/stop enclosing the entire call plus clock error | A missing search result; a complete interval that stops while the write might still be running | `incomplete_capture`, `capture_window_edge` |
| Which transcripts were withheld, lost or never collected? | An inventory from the collection system and known gaps; retain unmatched records in the denominator | Reporting only accuracy on available matching claims | `missing_transcript`; use recall over all twelve records alongside captured-event recall |
| Can another investigator reproduce the finding? | Original evidence, declarations, rule version, result citations and independently retained digest; rerun from raw inputs | A polished docket alone; hashes interpreted as proof that the source was truthful | Rerun the stress command; separately use `eg verify --recompute` for graph-to-docket consistency |

For real incidents, preserve unresolved findings as evidence requests. A failure to
attribute a mutation does not establish that it never occurred. A complete matching
receipt does not establish intent, agent identity, or the absence of an external
copy of model weights. This prototype cannot settle those questions.
