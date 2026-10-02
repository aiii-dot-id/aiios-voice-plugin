# On-demand waveform recording

`recording.record({})` saves the last buffered microphone recording to a
plugin-private WAV file and returns the saved-file path, sample count, and
SHA-256 digest. That is the complete caller workflow. STT finalization,
speaker attribution, pre-arming, and a second confirmation are not prerequisites.

The buffer contains at most the latest 30 seconds of admitted 16 kHz mono
microphone PCM. It remains available after the speech session closes and is
single-use: a second call without new microphone audio cannot replay it.
The call completes only after the host reports durable publication and the
plugin verifies readback. `recording.status({})` can re-read the last receipt
but is not needed to complete `recording.record`.

The generated path is fixed under the plugin's private `recordings/` directory.
The caller cannot choose a path, overwrite another recording, or receive WAV
bytes through the tool result. The signal may include playback from public
videos and is not a speaker-separated track or proof of identity.

`recording.list({})` returns the IDs and sizes of saved private WAV files,
without returning their audio. `recording.delete({"recording_id":"..."})`
removes one exact known ID after operator confirmation, including IDs absent
from the current bounded listing. These operations
work after the microphone closes and make saved-recording retention manageable;
they do not delete interrupted `.pending` uploads. The caller cannot supply
a path or use a partial ID.

The inventory reports `truncated` when the host returned only its first 1,024
directory entries. It never presents that page as the whole store. Deletion
checks only its exact regular-file target and therefore remains usable when
the directory exceeds the listing limit. After deleting unwanted recordings,
list again to expose the remaining entries.

`recording.prune({})` is a separate, confirmed retention operation. It deletes
only exact-format `.pending` uploads in the plugin's private recording and UID
directories whose host modification time is at least two minutes old. A live
upload has a 30-second publication deadline; younger, symlinked, unrecognized
or unlisted files are left alone. The receipt counts deleted files
and their last-listed sizes, and reports `truncated` if unexamined entries
remain. Repeat after productive deletions; if no eligible stages were removed,
inspect retention instead of looping. If a failed upload is too recent, the operator can
repeat the operation after the age threshold. This does not remove saved WAVs;
use `recording.delete` for those.

Background waveform publication and UID reads share one serialized storage
transaction. Contention waits within the original read/publication deadline;
it is not itself a failed speaker match or recording. Cancellation wakes the
owner and waiters, and a waiter cannot cross into a reopened session. Stop and
status remain on the independent control lane. Explicit speaker management is
refused while recording publication is outstanding, as recording already is
while explicit management is outstanding.

`scripts/prove_recording_waveform.py` exercises the native worker and private
broker with synthetic PCM, including recording *before* any STT final and after
session closure. It does not claim browser, real-microphone, or speaker-quality
qualification.
