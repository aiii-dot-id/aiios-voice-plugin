// Isolated integration-only access to the production activation binder.
// Loaded through Go -overlay, never compiled into a distributed AII OS build.
package pluginhost

import "github.com/aiii-dot-id/aii-os/internal/supervisor"

func VoiceBrowserProofBind(sup *supervisor.Supervisor) (*ActivePlugin, func(), error) {
	ap := &ActivePlugin{ID: "voice.browser-real-engine-proof", sup: sup}
	if err := ap.bindVoiceSession(sup); err != nil {
		return nil, nil, err
	}
	return ap, ap.sessionCancel, nil
}
