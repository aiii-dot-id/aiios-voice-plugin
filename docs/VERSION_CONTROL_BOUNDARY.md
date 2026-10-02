# Source and evidence version-control boundary

The repository records the information required to understand, reproduce, and
falsify a result:

- source code and tests;
- protocols, claim rules, and evaluation contracts;
- acquisition, training, experiment, and artifact manifests;
- normalized research decisions and human-readable reports;
- compact raw and aggregate results that contain no restricted payloads or
  secrets.

It does not record production, candidate, teacher, or third-party model
weights; downloaded corpora; Hugging Face caches; virtual environments;
build/signing products; local databases; or consented recordings. Those
objects remain outside Git and are named by exact size, digest, revision, and
license in tracked manifests.

A narrowly bounded exception permits tiny project-generated synthetic model
payloads when the bytes themselves are required to falsify a conformance or
exact-resume claim. Such a fixture must contain no human or third-party data,
must be independently reproducible from tracked source, and must remain below
1 MiB. Opaque training checkpoints remain outside source history even when a
compact canonical exported payload is retained.

This boundary is not permission to omit evidence. A result whose required
object cannot be published must still carry its immutable identifier,
provenance, access rule, and an independently checkable derivation. Published
release artifacts should live in a content-addressed artifact store or release
asset channel rather than being smuggled into source history.

No remote is implied by local initialization. Adding a remote or pushing is a
separate operator action.
