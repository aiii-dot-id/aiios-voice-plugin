# SDK session regressions

These tests exercise the pinned SDK's response reservation and flush behavior.
The public SDK source archive omits tests. The Voice test runner applies these
fixtures through Go's compiler overlay to the exact archived public production
source; it does not modify that source or ship these fixtures in the carrier.
All twelve named regressions must execute and pass. A missing test is a failure.
