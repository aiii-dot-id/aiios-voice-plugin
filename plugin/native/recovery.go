package main

import (
	"encoding/json"
	"errors"
)

// validateRecovery admits exactly the digests speaker.list.recovery reports:
// the enrollment and captures files always, the speaker registry only when
// that observation names it. The engine inspects again and refuses changed
// bytes; a confirmation that omits a reported registry changes nothing.
func validateRecovery(raw json.RawMessage) error {
	var refs map[string]string
	if json.Unmarshal(raw, &refs) != nil {
		return errors.New("recovery requires the observed digests from speaker.list")
	}
	want := 2
	if registry, named := refs["speaker_registry_sha256"]; named {
		if !hexDigest(registry) {
			return errors.New("recovery requires the observed speaker registry digest from speaker.list")
		}
		want = 3
	}
	if len(refs) != want {
		return errors.New("recovery requires the observed digests from speaker.list")
	}
	for _, key := range []string{"enrollment_sha256", "captures_sha256"} {
		value, present := refs[key]
		if !present || (value != "absent" && !hexDigest(value)) {
			return errors.New("recovery requires an observed digest or absent for each store")
		}
	}
	return nil
}
