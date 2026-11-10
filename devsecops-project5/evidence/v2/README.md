# Secured evidence

`native/` contains Phase 3 native control validation attempts, preserved command
transcripts, pre/post functional checks, comparison assessment and named-cluster
cleanup verification. V1 evidence remains unchanged in `../v1/`.

The final advanced architecture retains only the validated Kyverno admission
policies. Cilium and Linkerd were abandoned due to project scope and
operational complexity; their diagnostic evidence is retained under
`advanced/` but neither tool is part of the deployed architecture.

See the [Phase 3 native security assessment](native/SECURITY-ASSESSMENT.md),
the [final advanced assessment](advanced/SECURITY-ASSESSMENT.md), and the
[passing authoritative run](native/20261109T221358Z-f1f1b7ae/).
