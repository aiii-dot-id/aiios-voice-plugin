import Foundation
import AVFoundation
import AudioToolbox
import CoreAudio
import AudioRing
import CryptoKit

enum HostError: Error, CustomStringConvertible {
    case message(String)
    var description: String { switch self { case let .message(text): return text } }
}
func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw HostError.message(message) }
}
func checked(_ status: OSStatus, _ label: String) throws {
    try require(status == noErr, "\(label): OSStatus \(status)")
}
func propertyAddress(_ selector: AudioObjectPropertySelector,
                     _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}
func stringProperty(_ device: AudioDeviceID, _ selector: AudioObjectPropertySelector) throws -> String {
    var address = propertyAddress(selector); var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    try checked(AudioObjectGetPropertyData(device, &address, 0, nil, &size, &value), "read device string")
    guard let value else { throw HostError.message("missing device string") }
    return value.takeRetainedValue() as String
}
func channelCount(_ device: AudioDeviceID, _ scope: AudioObjectPropertyScope) throws -> Int {
    var address = propertyAddress(kAudioDevicePropertyStreamConfiguration, scope); var size: UInt32 = 0
    try checked(AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size), "read channel configuration size")
    let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
    defer { raw.deallocate() }
    try checked(AudioObjectGetPropertyData(device, &address, 0, nil, &size, raw), "read channel configuration")
    return UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
        .reduce(0) {$0 + Int($1.mNumberChannels)}
}
struct Device {
    let id: AudioDeviceID; let uid: String; let name: String; let inputs: Int; let outputs: Int
    var json: [String: Any] { ["id": id, "uid": uid, "name": name, "input_channels": inputs, "output_channels": outputs] }
}
func devices() throws -> [Device] {
    var address = propertyAddress(kAudioHardwarePropertyDevices); var size: UInt32 = 0
    try checked(AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size), "device list size")
    var ids = [AudioDeviceID](repeating: 0, count: Int(size)/MemoryLayout<AudioDeviceID>.size)
    try checked(AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids), "device list")
    return try ids.map { id in
        Device(id: id, uid: try stringProperty(id, kAudioDevicePropertyDeviceUID),
               name: try stringProperty(id, kAudioObjectPropertyName),
               inputs: try channelCount(id, kAudioObjectPropertyScopeInput),
               outputs: try channelCount(id, kAudioObjectPropertyScopeOutput))
    }
}
func defaultDevices() throws -> [String: UInt32] {
    var result = [String: UInt32]()
    for (key, selector) in [("input", kAudioHardwarePropertyDefaultInputDevice), ("output", kAudioHardwarePropertyDefaultOutputDevice)] {
        var address = propertyAddress(selector); var value: UInt32 = 0; var size: UInt32 = 4
        try checked(AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &value), "default device")
        result[key] = value
    }
    return result
}
func outputJSON(_ object: [String: Any]) {
    do {
        var data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        data.append(10); try FileHandle.standardOutput.write(contentsOf: data)
    } catch { exit(74) }
}

final class Host {
    let engine = AVAudioEngine(), player = AVAudioPlayerNode()
    let worker = DispatchQueue(label: "id.aiii.voice.native-control", qos: .userInteractive)
    let capture = vf_queue_create()!, render = vf_queue_create()!
    let frame = UnsafeMutablePointer<VFFrame>.allocate(capacity: 1)
    let directory: URL, duration: Double, emitAudio: Bool, stdio: Bool, gain: Float
    let inputDevice: Device, outputDevice: Device, processing: Bool
    var timer: DispatchSourceTimer?, trace: FileHandle!, micFile: FileHandle!, renderFile: FileHandle!
    var ready = false, ending = false, ended = false, failure: String?, eventNumber = 0
    var microphoneFrames: UInt64 = 0, renderFrames: UInt64 = 0, pendingFrames = 0, generation = 0
    var inputRate = 0.0, renderRate = 0.0, started = 0.0, defaults = [String: UInt32]()
    var activeID: String?, submitted: Int = 0, completed: Int = 0, synthesisDone = false
    var cancelledIDs = Set<String>()
    var lastInputTime: Double?, lastRenderTime: Double?
    var lastInputCount = 0, lastRenderCount = 0
    var maxCaptureDepth: UInt64 = 0, maxRenderDepth: UInt64 = 0
    var configurationObserver: NSObjectProtocol?
    var aggregateDevice: AudioDeviceID = 0
    var inputTapInstalled = false, renderTapInstalled = false
    var setupPhase = "not_started"
    let ttsFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)!

    init(input: Device, output: Device, directory: URL, duration: Double,
         processing: Bool, emitAudio: Bool, stdio: Bool, gain: Float) {
        self.inputDevice = input; self.outputDevice = output; self.directory = directory
        self.duration = duration; self.processing = processing; self.emitAudio = emitAudio; self.stdio = stdio; self.gain = gain
        frame.initialize(to: VFFrame())
    }
    func event(_ type: String, _ fields: [String: Any] = [:]) {
        eventNumber += 1
        var row = fields; row["type"] = type; row["sequence"] = eventNumber
        row["observed_host_ticks"] = mach_absolute_time()
        do {
            var data = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]); data.append(10)
            if let trace { try trace.write(contentsOf: data) }
            outputJSON(row)
        } catch { fail("evidence write failed: \(error)") }
    }
    func openFile(_ name: String) throws -> FileHandle {
        let url = directory.appendingPathComponent(name)
        let fd = open(url.path, O_WRONLY | O_CREAT | O_EXCL, S_IRUSR | S_IWUSR)
        try require(fd >= 0, "cannot exclusively create \(name)")
        return FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    }
    func select(_ node: AVAudioIONode, _ id: AudioDeviceID, _ element: AudioUnitElement) throws {
        guard let unit = node.audioUnit else { throw HostError.message("missing hardware audio unit") }
        var requested = id
        try checked(AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global,
                                         element, &requested, UInt32(MemoryLayout<AudioDeviceID>.size)), "select audio device")
    }
    func selected(_ node: AVAudioIONode, _ element: AudioUnitElement) throws -> AudioDeviceID {
        guard let unit = node.audioUnit else { throw HostError.message("missing hardware audio unit") }
        var actual: AudioDeviceID = 0; var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        try checked(AudioUnitGetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global,
                                         element, &actual, &size), "read selected audio device")
        return actual
    }
    func start() throws {
        try require(!FileManager.default.fileExists(atPath: directory.path), "evidence directory already exists")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        trace = try openFile("events.jsonl"); micFile = try openFile("microphone-processed.f32le"); renderFile = try openFile("render-reference.f32le")
        defaults = try defaultDevices()
        setupPhase = "compose_private_device"
        var ioDevice = inputDevice.id
        if inputDevice.id != outputDevice.id {
            try require(inputDevice.outputs == 0 && outputDevice.inputs == 0,
                        "distinct duplex devices require explicit channel mapping; no implicit routing")
            let composition: [String: Any] = [
                kAudioAggregateDeviceNameKey: "AII Voice Private Duplex",
                kAudioAggregateDeviceUIDKey: "id.aiii.voice.private.\(UUID().uuidString)",
                kAudioAggregateDeviceIsPrivateKey: 1,
                kAudioAggregateDeviceSubDeviceListKey: [
                    [kAudioSubDeviceUIDKey: inputDevice.uid, kAudioSubDeviceDriftCompensationKey: 1],
                    [kAudioSubDeviceUIDKey: outputDevice.uid, kAudioSubDeviceDriftCompensationKey: 0],
                ],
                kAudioAggregateDeviceMainSubDeviceKey: outputDevice.uid,
            ]
            try checked(AudioHardwareCreateAggregateDevice(composition as CFDictionary, &aggregateDevice), "create process-private duplex device")
            ioDevice = aggregateDevice
        }
        let input = engine.inputNode, output = engine.outputNode
        // AVAudioEngine uses one duplex device. The Studio Display exposes
        // separate input/output endpoints, joined only inside this process.
        setupPhase = "select_before_voice_processing"
        try select(input, ioDevice, 0); try select(output, ioDevice, 0)
        setupPhase = "enable_voice_processing"
        try input.setVoiceProcessingEnabled(processing)
        setupPhase = "select_after_voice_processing"
        // VPIO replaces AUHAL and has distinct device elements: input 1,
        // output 0. An aggregate is not a valid VPIO endpoint. Bind the
        // physical devices after replacement; never change system defaults.
        if processing {
            try select(input, inputDevice.id, 1)
            try select(output, outputDevice.id, 0)
        }
        event("device_binding", ["requested_private_device": ioDevice,
              "input_element0": (try? selected(input, 0)) ?? 0, "input_element1": (try? selected(input, 1)) ?? 0,
              "output_element0": (try? selected(output, 0)) ?? 0, "output_element1": (try? selected(output, 1)) ?? 0])
        let selectedInput = processing ? inputDevice.id : ioDevice
        let selectedOutput = processing ? outputDevice.id : ioDevice
        let inputElement: AudioUnitElement = processing ? 1 : 0
        try require(try selected(input, inputElement) == selectedInput && selected(output, 0) == selectedOutput, "duplex selection did not stick")
        if processing {
            input.isVoiceProcessingAGCEnabled = false
            input.isVoiceProcessingBypassed = false
            input.isVoiceProcessingInputMuted = false
        }
        let inputFormat = input.outputFormat(forBus: 0)
        setupPhase = "connect_audio_graph"
        try require(inputFormat.channelCount > 0 && inputFormat.sampleRate > 0, "invalid microphone format")
        try require(inputFormat.commonFormat == .pcmFormatFloat32 && !inputFormat.isInterleaved, "microphone must provide planar Float32")
        inputRate = inputFormat.sampleRate
        engine.attach(player); engine.connect(player, to: engine.mainMixerNode, format: ttsFormat)
        // VPIO requires the engine-facing input and output formats to match.
        // Replacing its devices does not refresh the mixer's default 44.1k
        // connection. Bind it explicitly to the selected 48k input format.
        if processing { engine.connect(engine.mainMixerNode, to: output, format: inputFormat) }
        engine.mainMixerNode.outputVolume = gain
        let referenceFormat = engine.mainMixerNode.outputFormat(forBus: 0)
        renderRate = referenceFormat.sampleRate
        let captureQueue = capture, referenceQueue = render
        input.installTap(onBus: 0, bufferSize: 480, format: inputFormat) { buffer, time in
            let flags: UInt32 = (time.isHostTimeValid ? 1 : 0) | (time.isSampleTimeValid ? 2 : 0)
            _ = vf_queue_push(captureQueue, buffer.floatChannelData?[0], buffer.frameLength,
                              time.hostTime, Double(time.sampleTime), flags)
        }
        inputTapInstalled = true
        engine.mainMixerNode.installTap(onBus: 0, bufferSize: 480, format: referenceFormat) { buffer, time in
            let flags: UInt32 = (time.isHostTimeValid ? 1 : 0) | (time.isSampleTimeValid ? 2 : 0)
            _ = vf_queue_push(referenceQueue, buffer.floatChannelData?[0], buffer.frameLength,
                              time.hostTime, Double(time.sampleTime), flags)
        }
        renderTapInstalled = true
        setupPhase = "start_audio_engine"
        engine.prepare(); try engine.start(); player.play()
        try require(try selected(input, inputElement) == selectedInput && selected(output, 0) == selectedOutput, "device changed during engine start")
        started = ProcessInfo.processInfo.systemUptime; ready = true
        configurationObserver = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: nil) { [weak self] _ in
            self?.worker.async {
                guard let self, !self.ended else { return }
                // Device selection can publish a delayed startup notification.
                // Accept only an unchanged running graph, never a fallback.
                do {
                    try require(self.engine.isRunning, "audio engine stopped on configuration change")
                    try require(try self.selected(input, inputElement) == selectedInput && self.selected(output, 0) == selectedOutput,
                                "audio device changed; explicit restart required")
                    try require(input.outputFormat(forBus: 0) == inputFormat && self.engine.mainMixerNode.outputFormat(forBus: 0) == referenceFormat,
                                "audio format changed; explicit restart required")
                    self.event("configuration_verified", ["engine_running": true, "routing_unchanged": true])
                } catch { self.fail(String(describing: error)) }
            }
        }
        event("ready", ["input": inputDevice.json, "output": outputDevice.json,
              "input_sample_rate": inputRate, "render_sample_rate": renderRate,
              "input_channel_policy": "first-channel", "render_channel_policy": "first-channel-mono-source",
              "voice_processing_enabled": input.isVoiceProcessingEnabled,
              "voice_processing_bypassed": input.isVoiceProcessingBypassed,
              "agc_enabled": input.isVoiceProcessingAGCEnabled,
              "raw_microphone_available": false, "gain": gain,
              "input_presentation_latency_s": input.presentationLatency,
              "output_presentation_latency_s": output.presentationLatency,
              "clock": "CoreAudio host ticks; physical acoustic latency unqualified",
              "host_tick_frequency": AVAudioTime.hostTime(forSeconds: 1),
              "defaults_before": defaults, "private_aggregate_id": aggregateDevice, "evidence": directory.path])
        let source = DispatchSource.makeTimerSource(queue: worker)
        source.schedule(deadline: .now(), repeating: .milliseconds(5))
        source.setEventHandler { [weak self] in self?.tick() }; timer = source; source.resume()
    }
    func drain(_ queue: OpaquePointer, _ name: String, _ file: FileHandle, _ rate: Double) throws {
        while vf_queue_pop(queue, frame) != 0 {
            let count = Int(frame.pointee.count); let time = frame.pointee.sample_time
            try require(frame.pointee.time_flags == 3, "\(name) missing hardware timestamp")
            let last = name == "microphone" ? lastInputTime : lastRenderTime
            let previousCount = name == "microphone" ? lastInputCount : lastRenderCount
            if let last { try require(abs(time - last - Double(previousCount)) < 0.5, "\(name) sample-time discontinuity") }
            let start = name == "microphone" ? microphoneFrames : renderFrames
            let samples = UnsafeBufferPointer(start: vf_frame_samples(frame), count: count)
            try require(samples.allSatisfy { $0.isFinite && abs($0) <= 1.001 }, "invalid or clipped \(name) PCM")
            let data = Data(bytes: samples.baseAddress!, count: count*4)
            try file.write(contentsOf: data)
            var fields: [String: Any] = ["stream": name, "start_sample": start, "frames": count,
                "hardware_sample_time": time, "hardware_host_ticks": frame.pointee.host_time, "sample_rate": rate,
                "content_sha256": SHA256.hash(data: data).map {String(format: "%02x", $0)}.joined()]
            if emitAudio { fields["pcm_f32le"] = data.base64EncodedString() }
            event("audio", fields)
            if name == "microphone" {microphoneFrames += UInt64(count); lastInputTime = time; lastInputCount = count}
            else {renderFrames += UInt64(count); lastRenderTime = time; lastRenderCount = count}
        }
    }
    func tick() {
        guard !ended else { return }
        do {
            maxCaptureDepth = max(maxCaptureDepth, vf_queue_depth(capture)); maxRenderDepth = max(maxRenderDepth, vf_queue_depth(render))
            try require(vf_queue_drops(capture) == 0 && vf_queue_drops(render) == 0, "audio queue overflow/invalid callback; refusing silent drops")
            try drain(capture, "microphone", micFile, inputRate); try drain(render, "render", renderFile, renderRate)
            if ProcessInfo.processInfo.systemUptime-started >= duration { stop(reason: "duration_limit") }
            else if ending && pendingFrames == 0 { stop(reason: "finished") }
        } catch { fail(String(describing: error)) }
    }
    func enqueue(_ id: String, _ samples: [Float], endSample: Int) throws {
        try require(ready && !ended && !ending, "host is not accepting playback")
        try require(!id.isEmpty && id.count <= 80, "invalid synthesis id")
        if cancelledIDs.contains(id) { return } // Late PCM cannot revive a cancelled generation.
        try require(!samples.isEmpty && samples.count <= 24000 && samples.allSatisfy { $0.isFinite && abs($0) <= 1 }, "invalid playback chunk")
        try require(pendingFrames + samples.count <= 96000, "playback exceeds four-second bound")
        if let activeID { try require(activeID == id && !synthesisDone, "finish or cancel prior synthesis before replacement") }
        else { activeID = id; submitted = 0; completed = 0; synthesisDone = false; event("playback_start", ["synthesis_id": id]) }
        try require(endSample == submitted + samples.count, "playback samples must be contiguous")
        let buffer = AVAudioPCMBuffer(pcmFormat: ttsFormat, frameCapacity: AVAudioFrameCount(samples.count))!
        buffer.frameLength = buffer.frameCapacity
        samples.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
        submitted = endSample; pendingFrames += samples.count; let token = generation
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            self?.worker.async {
                guard let self, !self.ended, self.generation == token, self.activeID == id else { return }
                self.pendingFrames -= samples.count; self.completed = endSample
                self.event("played", ["synthesis_id": id, "samples": endSample, "completion_semantics": "AVAudioPlayerNode.dataPlayedBack; not an acoustic measurement"])
                self.finishPlaybackIfDrained()
            }
        }
        if !player.isPlaying { player.play() }
    }
    func finishPlaybackIfDrained() {
        if synthesisDone && pendingFrames == 0, let id = activeID {
            event("playback_stop", ["synthesis_id": id, "reason": "drained", "completed_samples": completed])
            activeID = nil
        }
    }
    func cancel(_ id: String?, reason: String) {
        if let id {
            // Fence even if the first PCM on the other lane has not arrived.
            guard id.count <= 80 && !id.isEmpty && (cancelledIDs.contains(id) || cancelledIDs.count < 4096) else {
                fail("cancel identity/bounded tombstone limit"); return
            }
            cancelledIDs.insert(id)
        } else if let activeID { cancelledIDs.insert(activeID) }
        if let id, id != activeID { event("cancel_ignored", ["synthesis_id": id, "reason": "not_active"]); return }
        guard let current = activeID else { return }
        generation += 1; player.stop(); pendingFrames = 0
        event("playback_stop", ["synthesis_id": current, "reason": reason, "completed_samples": completed,
                              "submitted_samples": submitted, "physical_stop_measured": false])
        activeID = nil; synthesisDone = false
        if !ended { player.play() }
    }
    func command(_ row: [String: Any]) throws {
        guard let type = row["type"] as? String else { throw HostError.message("command needs type") }
        switch type {
        case "audio":
            guard let id = row["synthesis_id"] as? String, let encoded = row["pcm_f32le"] as? String,
                  let data = Data(base64Encoded: encoded), data.count % 4 == 0,
                  let number = row["end_sample"] as? NSNumber,
                  CFGetTypeID(number) != CFBooleanGetTypeID(),
                  let end = row["end_sample"] as? Int else { throw HostError.message("malformed audio command") }
            let samples = data.withUnsafeBytes { raw in (0..<(raw.count/4)).map { raw.loadUnaligned(fromByteOffset: $0*4, as: Float.self) } }
            try enqueue(id, samples, endSample: end)
        case "synthesis_done":
            guard let id = row["synthesis_id"] as? String else { throw HostError.message("completion needs synthesis id") }
            if cancelledIDs.contains(id) { return }
            try require(id == activeID, "completion names inactive synthesis")
            synthesisDone = true; finishPlaybackIfDrained()
        case "cancel":
            if let value = row["synthesis_id"] {
                guard let id = value as? String, !id.isEmpty else { throw HostError.message("cancel synthesis_id must be a nonempty string") }
                cancel(id, reason: "control_cancel")
            } else { cancel(nil, reason: "control_cancel") }
        case "finish": ending = true; synthesisDone = true; finishPlaybackIfDrained()
        case "stop": stop(reason: "operator_stop")
        default: throw HostError.message("unknown command: \(type)")
        }
    }
    func playFile(_ path: String) throws {
        let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
        try require(file.processingFormat.sampleRate == 24000 && file.processingFormat.channelCount == 1, "probe requires mono 24 kHz WAV")
        try require(file.length > 0 && file.length <= 96000, "probe WAV must contain at most four seconds")
        let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length))!
        try file.read(into: buffer)
        let samples = Array(UnsafeBufferPointer(start: buffer.floatChannelData![0], count: Int(buffer.frameLength)))
        var offset = 0
        while offset < samples.count {
            let end = min(offset+7680, samples.count); try enqueue("probe", Array(samples[offset..<end]), endSample: end); offset = end
        }
        synthesisDone = true
    }
    func fail(_ reason: String) {
        guard !ended else { return }; failure = reason
        stop(reason: "failure")
    }
    func stop(reason: String) {
        guard !ended else { return }
        cancel(nil, reason: reason); ended = true; timer?.cancel(); engine.stop()
        if let configurationObserver { NotificationCenter.default.removeObserver(configurationObserver) }
        if inputTapInstalled { engine.inputNode.removeTap(onBus: 0) }
        if renderTapInstalled { engine.mainMixerNode.removeTap(onBus: 0) }
        do {
            try drain(capture, "microphone", micFile, inputRate); try drain(render, "render", renderFile, renderRate)
            if failure == nil && ready && (microphoneFrames == 0 || renderFrames == 0) { failure = "native engine did not supply both audio streams" }
            let currentDefaults = try defaultDevices()
            if currentDefaults != defaults { failure = "system defaults changed during session" }
            if aggregateDevice != 0 {
                try checked(AudioHardwareDestroyAggregateDevice(aggregateDevice), "destroy private duplex device")
                aggregateDevice = 0
            }
            var report: [String: Any] = ["type": "complete", "status": failure == nil ? "passed" : "failed",
                "scope": "native device transport; acoustic admission is a separate gate", "reason": reason,
                "microphone_frames": microphoneFrames, "render_frames": renderFrames,
                "input_sample_rate": inputRate, "render_sample_rate": renderRate,
                "capture_drops": vf_queue_drops(capture), "render_drops": vf_queue_drops(render),
                "max_capture_queue": maxCaptureDepth, "max_render_queue": maxRenderDepth,
                "defaults_before": defaults, "defaults_after": currentDefaults,
                "private_aggregate_removed": aggregateDevice == 0,
                "duration_s": started == 0 ? 0 : ProcessInfo.processInfo.systemUptime-started]
            if let failure { report["error"] = failure }
            event("complete", report)
            let bytes = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys, .prettyPrinted])
            try bytes.write(to: directory.appendingPathComponent("report.json"), options: .withoutOverwriting)
            try micFile?.close(); try renderFile?.close(); try trace?.close()
        } catch { outputJSON(["type": "error", "reason": String(describing: error)]); exit(1) }
        exit(failure == nil ? 0 : 1)
    }
}

func arguments() throws -> (String, [String: String]) {
    let args = Array(CommandLine.arguments.dropFirst()); guard let mode = args.first else { return ("help", [:]) }
    var values = [String: String](); var i = 1
    while i < args.count {
        try require(args[i].hasPrefix("--") && i+1 < args.count && !args[i+1].hasPrefix("--"), "arguments require --name value")
        try require(values[args[i]] == nil, "duplicate argument \(args[i])")
        values[args[i]] = args[i+1]; i += 2
    }
    let allowed: Set<String> = ["--input-uid", "--output-uid", "--output", "--duration", "--voice-processing", "--emit-audio", "--stdio", "--gain", "--play-file", "--play-at", "--cancel-at", "--control-fifo"]
    try require(Set(values.keys).isSubset(of: allowed), "unknown option")
    return (mode, values)
}
do {
    let (mode, args) = try arguments()
    if mode == "devices" { outputJSON(["devices": try devices().map(\.json), "defaults": try defaultDevices()]); exit(0) }
    if mode == "permission" { outputJSON(["microphone_authorization": AVCaptureDevice.authorizationStatus(for: .audio).rawValue, "bundle_id": Bundle.main.bundleIdentifier ?? "none", "bundle_path": Bundle.main.bundlePath]); exit(0) }
    if mode == "help" { outputJSON(["usage": "aii-audio-host devices | run --input-uid UID --output-uid UID --output NEW_DIRECTORY [--duration 15] [--voice-processing on|off] [--play-file MONO_24K_WAV] [--play-at 2] [--cancel-at SECONDS] [--stdio on] [--emit-audio on]"]); exit(0) }
    try require(mode == "run", "unknown mode")
    guard let inputUID = args["--input-uid"], let outputUID = args["--output-uid"], let destination = args["--output"] else { throw HostError.message("explicit input/output UIDs and new evidence directory required") }
    let inventory = try devices()
    guard let input = inventory.first(where: {$0.uid == inputUID && $0.inputs > 0}),
          let output = inventory.first(where: {$0.uid == outputUID && $0.outputs > 0}) else { throw HostError.message("requested devices not present; no default fallback") }
    let duration = Double(args["--duration"] ?? "15") ?? 0; let gain = Float(args["--gain"] ?? "0.6") ?? -1
    try require(duration.isFinite && duration >= 1 && duration <= 1860 && gain.isFinite && gain >= 0 && gain <= 1, "invalid duration or gain")
    for key in ["--voice-processing", "--emit-audio", "--stdio"] { if let value = args[key] { try require(["on", "off"].contains(value), "\(key) must be on or off") } }
    if AVCaptureDevice.authorizationStatus(for: .audio) != .authorized {
        let semaphore = DispatchSemaphore(value: 0); var allowed = false
        AVCaptureDevice.requestAccess(for: .audio) { granted in allowed = granted; semaphore.signal() }
        let deadline = Date().addingTimeInterval(45)
        while semaphore.wait(timeout: .now()) != .success {
            try require(Date() < deadline, "microphone permission prompt timed out")
            // TCC permission delivery needs the main run loop; never block it
            // on the response semaphore before starting the application loop.
            RunLoop.current.run(until: Date().addingTimeInterval(0.02))
        }
        try require(allowed, "microphone permission not granted; authorization=\(AVCaptureDevice.authorizationStatus(for: .audio).rawValue)")
    }
    let host = Host(input: input, output: output, directory: URL(fileURLWithPath: destination), duration: duration,
                    processing: args["--voice-processing"] != "off", emitAudio: args["--emit-audio"] == "on",
                    stdio: args["--stdio"] == "on", gain: gain)
    host.worker.async {
        do {
            try host.start()
            if let file = args["--play-file"] {
                guard let at = Double(args["--play-at"] ?? "2"), at >= 0 && at < duration else { throw HostError.message("invalid play time") }
                host.worker.asyncAfter(deadline: .now()+at) { do { try host.playFile(file) } catch { host.fail(String(describing: error)) } }
            }
            if let text = args["--cancel-at"] {
                guard let at = Double(text), at >= 0 && at < duration else { throw HostError.message("invalid cancel time") }
                host.worker.asyncAfter(deadline: .now()+at) { host.cancel(nil, reason: "scheduled_probe_cancel") }
            }
        } catch {
            outputJSON(["type": "error", "phase": host.setupPhase, "reason": String(describing: error)])
            if host.trace != nil { host.fail("\(host.setupPhase): \(error)") }
            exit(1)
        }
    }
    func receiveCommands(_ descriptor: Int32, urgent: Bool) {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                var pending = Data()
                let commandSlots = DispatchSemaphore(value: 32)
                var inputBytes = [UInt8](repeating: 0, count: 4096)
                while true {
                    // FileHandle.read(upToCount:) can wait to fill its request
                    // on a pipe. POSIX read returns the available short read:
                    // a 41-byte cancel must not wait for the next audio chunk.
                    let count = read(descriptor, &inputBytes, inputBytes.count)
                    if count < 0 && errno == EINTR { continue }
                    try require(count >= 0, "control pipe read failed: errno \(errno)")
                    if count == 0 { break }
                    pending.append(contentsOf: inputBytes.prefix(count))
                    while let newline = pending.firstIndex(of: 10) {
                        let rowData = pending.prefix(upTo: newline); pending.removeSubrange(...newline)
                        try require(rowData.count <= 140000, "control message exceeds bound")
                        guard let row = try JSONSerialization.jsonObject(with: rowData) as? [String: Any] else { throw HostError.message("control message must be object") }
                        if urgent { try require(["cancel", "stop"].contains(row["type"] as? String ?? ""), "urgent lane only admits cancel/stop") }
                        try require(commandSlots.wait(timeout: .now()) == .success, "control queue exceeded 32 commands")
                        host.worker.async {
                            defer { commandSlots.signal() }
                            do { try host.command(row) } catch { host.fail(String(describing: error)) }
                        }
                    }
                    try require(pending.count <= 140000, "control message exceeds bound")
                }
                try require(pending.isEmpty, "control pipe ended with an incomplete JSON line")
                host.worker.async { host.stop(reason: "control_eof") }
            } catch { host.worker.async { host.fail(String(describing: error)) } }
        }
    }
    if host.stdio { receiveCommands(STDIN_FILENO, urgent: false) }
    if let path = args["--control-fifo"] {
        try require(host.stdio, "control fifo requires stdio")
        let descriptor = open(path, O_RDWR | O_NOFOLLOW)
        try require(descriptor >= 0, "cannot open private control fifo")
        var info = stat()
        try require(fstat(descriptor, &info) == 0 && (info.st_mode & S_IFMT) == S_IFIFO && info.st_uid == getuid(), "control endpoint must be an owned fifo")
        receiveCommands(descriptor, urgent: true)
    }
    dispatchMain()
} catch { outputJSON(["type": "error", "reason": String(describing: error)]); exit(1) }
