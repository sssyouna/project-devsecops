# Phase 4 — Final advanced security assessment

**Final architecture:** Kubernetes native controls, Calico, Kubernetes
NetworkPolicies, PostgreSQL least privilege, and Kyverno. Cilium and Linkerd
are abandoned by project scope and operational complexity and are not part of
the deployed architecture.

This assessment supplements the approved native baseline in
[`evidence/v2/native/SECURITY-ASSESSMENT.md`](../native/SECURITY-ASSESSMENT.md).
The V1 evidence and the authoritative Phase 3 native run remain unchanged.

## Verified controls

### Native baseline

The Phase 3 authoritative run passed all eight native security scenarios and
the pre/post functional checks. It verified RBAC and dedicated tokenless
ServiceAccounts, Restricted Pod Security Admission, hardened security
contexts, Calico NetworkPolicies, and PostgreSQL least privilege.

### Kyverno

Kyverno v1.19.2 was installed from the pinned manifest. Its SHA-256 is
recorded in [`results.json`](./results.json). Three focused policies were
validated:

| Policy | Mode | Verified value |
| --- | --- | --- |
| `project5-approved-images` | Enforce | Rejects image repositories outside the approved Project 5 image set. |
| `project5-resource-requirements` | Enforce | Requires CPU and memory requests and limits for Project 5 containers. |
| `project5-immutable-protected-images` | Audit | Reports intended digest use for explicitly opted-in workloads; it does not prevent deployment. |

These policies complement PSA. They do not duplicate PSA's controls for
privilege, root identity, capabilities, seccomp, or host access.

## Kyverno validation results

| Scenario | Expected | Observed | Classification |
| --- | --- | --- | --- |
| PSA-compliant approved image with complete resources | Admit | Admitted and created | Verified compatibility |
| PSA-compliant unapproved image | Deny | Rejected by `project5-approved-images`, rule `require-approved-container-images` | Verified admission prevention |
| PSA-compliant image without resource limits | Deny | Rejected by `project5-resource-requirements`, rule `require-container-resources` | Verified admission prevention |
| Opt-in digest policy with a tag | Admit and report | Accepted; `digest-audit-apply.txt` contains no blocking error | Verified audit-only behavior; no finding is claimed |

The negative manifests were PSA-compliant. Their responses identify the
Kyverno webhook and policy rule, so the rejections are attributable to
Kyverno rather than PSA.

The Kyverno-specific tests were successful. The final integrated attempt also
passed frontend availability, backend health, inventory CRUD, PostgreSQL
persistence, all eight native scenario checks, and Kyverno admission checks.
The corrected native runner passed regression integrity with
`runtime_expectation_met=true`. The opt-in digest workload was accepted without blocking and a matching
namespaced `PolicyReport` was observed before Pod deletion. It recorded
`result=fail` for `project5-immutable-protected-images` /
`require-digests-on-protected-workloads`. The complete targeted evidence is
retained under [`digest-audit-validation/`](./digest-audit-validation/).
All required integration criteria are therefore verified.

## Abandoned experiments

### Cilium

Cilium is abandoned due to project scope and operational complexity. Existing
diagnostic evidence is retained under
[`cilium/`](./cilium/) for traceability, but it is not evidence that Cilium is
incompatible with the VM and it is not included in the architecture.

### Linkerd

Linkerd is abandoned due to project scope and operational complexity. The
retained attempts reached setup and prerequisite failures but did not verify
control-plane readiness, proxy injection, service identity, or mTLS. Existing
diagnostic evidence is retained under [`linkerd/`](./linkerd/). No Linkerd
security benefit or incompatibility claim is made.

### Runtime detection

Falco and Tetragon were not deployed. No runtime detection result or
performance claim is made.

## V1 versus V2 comparison

| Capability | V1 insecure | V2 native | V2 final architecture |
| --- | --- | --- | --- |
| Workload identity and RBAC | Shared or excessive access was demonstrated | Dedicated ServiceAccounts, no mounted application tokens, least privilege | Same native controls |
| Pod security | Dangerous settings were accepted | Restricted PSA and hardened security contexts rejected or prevented tested cases | Same native controls |
| Network isolation | Unrestricted east-west paths were demonstrated | Calico and NetworkPolicies enforce only required application paths | Same native controls |
| Database privilege | Application account was a superuser | CRUD-only `inventory_app`; administrative/file/DDL operations denied | Same native controls |
| Image source policy | No admission policy | No native image-source policy | Kyverno enforces approved repositories |
| Resource budgeting | Unspecified | Workloads declare resources | Kyverno requires requests and limits for future Project 5 containers |
| Immutable image references | No policy | No policy | Opt-in Kyverno audit only; not prevention |
| Runtime detection | None | None | None verified |

## Six security-justification questions — Kyverno

### 1. Quel risque permet-il de réduire ?

Kyverno réduit le risque d’introduire une image provenant d’un dépôt non
approuvé et le risque de déployer un conteneur sans demandes ni limites CPU et
mémoires. La politique de digest réduit seulement par audit le risque lié aux
références mutables pour les workloads qui s’inscrivent explicitement dans la
politique. Les deux premières protections sont expérimentalement vérifiées;
l’audit digest est vérifié comme non bloquant, sans constat PolicyReport retenu.

### 2. Quel mécanisme existant complète-t-il ?

Il complète PSA, RBAC, les ServiceAccounts dédiés, les security contexts et les
NetworkPolicies. PSA traite principalement les propriétés de sécurité du Pod;
Kyverno traite ici la provenance déclarée des images et le budget de
ressources. Il ne remplace aucun de ces mécanismes.

### 3. Existe-t-il une redondance ?

Il n’y a pas de redondance intentionnelle avec les contrôles PSA validés.
Certaines règles peuvent être évaluées dans la même chaîne d’admission, mais
les politiques retenues ciblent des propriétés que PSA n’impose pas. La portée
reste limitée aux namespaces et workloads couverts par les règles.

### 4. Quelle nouvelle surface d’attaque introduit-il ?

Kyverno ajoute des CRDs, des contrôleurs et un webhook d’admission. Une erreur
de configuration, une compromission du contrôleur ou une indisponibilité du
webhook peut affecter les décisions d’admission et la disponibilité des
déploiements. Les permissions et la disponibilité du webhook doivent donc être
surveillées.

### 5. Quel est son coût opérationnel ?

Le coût est modéré mais non nul: installation et mise à jour des CRDs,
maintenance des règles, latence supplémentaire d’admission et dépendance au
webhook. La mesure CPU/mémoire par contrôleur n’a pas été conservée; aucun
chiffre de surcharge n’est donc inventé. L’application initiale a nécessité
le mode server-side à cause de la taille des annotations de CRD.

### 6. Que se passe-t-il si ce composant devient indisponible ou est compromis ?

La protection supplémentaire de Kyverno peut disparaître ou bloquer de
nouveaux déploiements selon la configuration du webhook. Les contrôles natifs
PSA, RBAC, security contexts, NetworkPolicies et PostgreSQL restent les
barrières indépendantes. Si Kyverno est compromis, ses règles et ses
permissions doivent être traitées comme une nouvelle dépendance de confiance;
la portée limitée des policies réduit, sans supprimer, cet impact.

## Résiduels, coûts et limites

- Les CRUD restent non authentifiés sur le chemin explicitement autorisé;
  NetworkPolicy n'est pas une autorisation applicative.
- Les Secrets synthétiques ne constituent pas un système de gestion des
  secrets ni un chiffrement au repos.
- Il n'y a pas de TLS applicatif ou PostgreSQL démontré.
- La politique digest est en mode Audit et ne garantit pas l'immuabilité.
- La liste de dépôts approuvés est adaptée au laboratoire hors ligne, pas à
  une stratégie de provenance ou d'attestation de production.
- La détection runtime n'est pas validée.
- Aucun résultat de performance Kyverno par processus n'est conservé.
- The integrated run did not satisfy the native runner's required aggregate
  integrity invariant, despite every T01-T08 scenario reporting PASS. This is
  a validation workflow blocker, not evidence that an individual native
  control failed.

## Nettoyage et preuves

Les clusters expérimentaux ont été supprimés après les essais. Les preuves
sanitisées Kyverno sont conservées dans [`kyverno/`](./kyverno/), avec les
résultats machine-readable dans [`results.json`](./results.json) et les
mesures de ressources dans
[`resource-measurements.json`](./resource-measurements.json). Les diagnostics
Cilium et Linkerd sont conservés à titre historique, mais ne font pas partie
de l'architecture finale.
