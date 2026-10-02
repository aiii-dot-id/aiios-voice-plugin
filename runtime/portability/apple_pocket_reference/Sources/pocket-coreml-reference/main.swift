import CoreML
import CryptoKit
import Darwin
import FluidAudio
import Foundation
#if os(iOS)
import UIKit
#endif

// Private comparison harness. It does not implement the shipping SDK lane,
// does not activate microphone/playback, and does not change a live plugin.
enum ProbeError: Error { case refused(String) }

func memorySample() -> [String: UInt64] {
    var info = task_vm_info_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
    let status = withUnsafeMutablePointer(to: &info) { pointer in
        pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
        }
    }
    guard status == KERN_SUCCESS else { return [:] }
    return ["phys_footprint": info.phys_footprint, "resident_size": info.resident_size,
            "resident_size_peak": info.resident_size_peak, "virtual_size": info.virtual_size]
}

func writeJSON(_ object: [String: Any], to path: URL) throws {
    let data = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: path, options: .withoutOverwriting)
}

func log(_ text: String) {
    FileHandle.standardError.write(Data((text + "\n").utf8))
}

func audioData(_ pcm: [Float]) -> Data { pcm.withUnsafeBytes { Data($0) } }

func wave(_ pcm: [Float]) -> Data {
    var data = Data()
    func word<T: FixedWidthInteger>(_ value: T) {
        var little = value.littleEndian
        withUnsafeBytes(of: &little) { data.append(contentsOf: $0) }
    }
    data.append(Data("RIFF".utf8)); word(UInt32(36 + pcm.count * 4))
    data.append(Data("WAVEfmt ".utf8)); word(UInt32(16)); word(UInt16(3)); word(UInt16(1))
    word(UInt32(24_000)); word(UInt32(96_000)); word(UInt16(4)); word(UInt16(32))
    data.append(Data("data".utf8)); word(UInt32(pcm.count * 4)); data.append(audioData(pcm))
    return data
}

struct CaseResult {
    var metrics: [String: Any]
    var pcm: [Float]
}

func runCase(_ name: String, manager: PocketTtsManager, directory: URL,
             texts: [String], voice: String = "alba", cancelAfter: Int? = nil) async throws -> CaseResult {
    let start = ProcessInfo.processInfo.systemUptime
    let session = try await manager.makeSession(voice: voice, temperature: 0.7, seed: 123)
    let sessionReady = ProcessInfo.processInfo.systemUptime
    for text in texts { session.enqueue(text) }
    session.finish()
    var pcm = [Float]()
    var frames = 0
    var firstMs: Double?
    var utterances = Set<Int>()
    var cancelMs: Double?
    var discardedAfterFence = 0
    var fence = false
    var cancellationObserved = false
    do {
      for try await frame in session.frames {
        if fence {
            discardedAfterFence += frame.samples.count
            continue
        }
        if firstMs == nil { firstMs = (ProcessInfo.processInfo.systemUptime - start) * 1000 }
        guard frame.samples.count == 1920, frame.samples.allSatisfy(\.isFinite) else {
            await session.cancel()
            throw ProbeError.refused("nonfinite or wrong-sized PCM in \(name)")
        }
        if let index = frame.utteranceIndex { utterances.insert(index) }
        pcm.append(contentsOf: frame.samples)
        frames += 1
        if frames > 1200 || ProcessInfo.processInfo.systemUptime - start > 120 {
            await session.cancel()
            throw ProbeError.refused("case budget exceeded: \(name)")
        }
        if let cancelAfter, frames == cancelAfter {
            // Fence delivery before asking upstream to retire. The upstream
            // cancel() awaits inference retirement: it is NOT our immediate
            // SDK cancellation admission operation.
            fence = true
            let mark = ProcessInfo.processInfo.systemUptime
            await session.cancel()
            cancelMs = (ProcessInfo.processInfo.systemUptime - mark) * 1000
        }
      }
    } catch is CancellationError {
        guard fence else { throw CancellationError() }
        cancellationObserved = true
    }
    let elapsed = ProcessInfo.processInfo.systemUptime - start
    let completions = await session.chunkCompletions
    if cancelAfter == nil {
        guard Set(completions.map(\.utteranceIndex)) == Set(texts.indices),
              completions.reduce(0, { $0 + $1.emittedFrames }) == frames,
              completions.allSatisfy({ $0.emittedFrames == $0.eosStep + $0.tailFrames }) else {
            throw ProbeError.refused("missing natural EOS or incomplete tail: \(name)")
        }
    }
    guard !pcm.isEmpty, pcm.contains(where: { abs($0) > 0.0001 }) else {
        throw ProbeError.refused("no non-silent PCM: \(name)")
    }
    if cancelAfter == nil && utterances != Set(texts.indices) {
        throw ProbeError.refused("missing utterance: \(name)")
    }
    let raw = audioData(pcm)
    try raw.write(to: directory.appendingPathComponent(name + ".f32"), options: .withoutOverwriting)
    try wave(pcm).write(to: directory.appendingPathComponent(name + ".wav"), options: .withoutOverwriting)
    var metrics: [String: Any] = [
        "name": name, "voice": voice, "texts": texts, "sample_rate": 24000,
        "started_uptime": start, "ended_uptime": ProcessInfo.processInfo.systemUptime,
        "samples": pcm.count, "frames": frames, "utterances": utterances.sorted(),
        "first_pcm_ms": firstMs ?? -1, "wall_seconds": elapsed,
        "session_create_ms": (sessionReady - start) * 1000,
        "synthesis_rtf": elapsed / (Double(pcm.count) / 24000),
        "pcm_sha256": SHA256.hash(data: raw).map { String(format: "%02x", $0) }.joined(),
        "memory_after": memorySample(), "discarded_after_cancel_fence_samples": discardedAfterFence,
        "cancellation_observed": cancellationObserved,
        "eos_proof": cancelAfter == nil ? "natural EOS plus complete tail" : "cancelled, not completed",
        "chunk_completions": completions.map { ["utterance": $0.utteranceIndex, "chunk": $0.chunkIndex,
                                               "eos_step": $0.eosStep, "tail_frames": $0.tailFrames,
                                               "emitted_frames": $0.emittedFrames] }
    ]
    if let cancelMs { metrics["cancel_retirement_ms"] = cancelMs }
    try writeJSON(metrics, to: directory.appendingPathComponent(name + ".json"))
    log("CASE_COMPLETE \(name) frames=\(frames) rtf=\(metrics["synthesis_rtf"] ?? "")")
    return CaseResult(metrics: metrics, pcm: pcm)
}

enum ReferenceProbe {
    static func proveBudgetFailure(manager: PocketTtsManager, output: URL) async throws -> [String: Any] {
        setenv("AII_POCKET_TEST_FRAME_LIMIT", "1", 1)
        defer { unsetenv("AII_POCKET_TEST_FRAME_LIMIT") }
        let session = try await manager.makeSession(voice: "alba", temperature: 0.7, seed: 123)
        session.enqueue("I kept the opening words. We can continue now.")
        session.finish()
        var samples = [Float]()
        var refusal: String?
        do {
            for try await frame in session.frames { samples.append(contentsOf: frame.samples) }
        } catch {
            refusal = String(describing: error)
        }
        let completions = await session.chunkCompletions
        guard let refusal, refusal.contains("frame budget exhausted before natural EOS and complete tail"),
              samples.count == 1920, completions.isEmpty else {
            throw ProbeError.refused("exhausted frame budget was not reported as a failure")
        }
        let data = audioData(samples)
        try data.write(to: output.appendingPathComponent("budget-exhaustion.f32"), options: .withoutOverwriting)
        return ["refused": true, "error": refusal, "samples": samples.count, "completion_count": completions.count,
                "pcm_sha256": SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()]
    }

    static func run(cache: URL, output: URL, compute: String) async throws {
        guard !FileManager.default.fileExists(atPath: output.path) else {
            throw ProbeError.refused("output must be new")
        }
        func units(_ name: String) throws -> MLComputeUnits {
            switch name {
            case "cpu": return .cpuOnly
            case "gpu": return .cpuAndGPU
            case "ane": return .cpuAndNeuralEngine
            default: throw ProbeError.refused("unknown compute mode")
            }
        }
        let modes = compute.split(separator: "-").map(String.init)
        guard modes.count == 1 || modes.count == 2 else { throw ProbeError.refused("unknown stage mode") }
        let prefillUnits = try units(modes[0])
        let generateUnits = try units(modes.last ?? "invalid")
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let t0 = ProcessInfo.processInfo.systemUptime
        let memoryBefore = memorySample()
        let manager = PocketTtsManager(directory: cache, placement: .aneState,
            computeUnits: PocketTtsComputeUnits(conditioner: prefillUnits, flowLM: generateUnits, mimiDecoder: .cpuOnly))
        log("MODEL_LOAD_BEGIN mode=\(compute)")
        try await manager.initialize()
        let loadSeconds = ProcessInfo.processInfo.systemUptime - t0
        try writeJSON(["load_seconds": loadSeconds, "memory_before": memoryBefore,
                       "memory_loaded": memorySample(), "requested_state_compute": compute,
                       "requested_prefill": modes[0], "requested_generate": modes.last ?? "invalid",
                       "requested_mimi_compute": "cpuOnly", "actual_execution_trace": false],
                      to: output.appendingPathComponent("load.json"))
        log("MODEL_LOAD_COMPLETE seconds=\(loadSeconds)")
        let texts = ["Please stop now. Keep the words cobalt lantern seventeen.",
                     "The voice is ready for another conversation."]
        let first = try await runCase("two-turns", manager: manager, directory: output, texts: texts)
        let repeated = try await runCase("two-turns-repeat", manager: manager, directory: output, texts: texts)
        guard first.pcm == repeated.pcm else { throw ProbeError.refused("same-history repeat differs") }
        let recoveryText = ["I kept the opening words. We can continue now."]
        let before = try await runCase("recovery-reference", manager: manager, directory: output, texts: recoveryText)
        let cancelled = try await runCase("cancel", manager: manager, directory: output,
            texts: [String(repeating: "This sentence makes a long reply that must stop when interrupted. ", count: 12)], cancelAfter: 4)
        let after = try await runCase("recovery-after-cancel", manager: manager, directory: output, texts: recoveryText)
        guard before.pcm == after.pcm else { throw ProbeError.refused("fresh recovery differs after cancellation") }
        let alternate = try await runCase("alternate-voice", manager: manager, directory: output,
                                          texts: recoveryText, voice: "azelma")
        guard alternate.pcm != before.pcm else { throw ProbeError.refused("voice selection inert") }
        // Run only in the non-diagnostic acceptance candidate. The unchanged
        // six-case panel remains comparable to the source-bound reference.
        let budget = try await proveBudgetFailure(manager: manager, output: output)
        let budgetRecovery = try await runCase("budget-recovery", manager: manager, directory: output, texts: recoveryText)
        guard budgetRecovery.pcm == before.pcm else { throw ProbeError.refused("budget failure poisoned recovery") }
        try writeJSON([
            "status": "reference_mechanics_pass_not_product_qualification",
            "source_revision": "fbc1b867a59a223f1b55cbc8fb20df7e1bdf7859",
            "private_source_variant": "fluidaudio-stage-isolation-r1",
            "model_revision": "91748676fe3c8b2eb3007b3125253bcd898202c3",
            "requested_state_compute": compute, "requested_mimi_compute": "cpuOnly",
            "actual_execution_trace": false, "load_seconds": loadSeconds,
            "same_history_repeat_exact": true, "fresh_recovery_exact": true,
            "budget_failure": budget, "budget_recovery": budgetRecovery.metrics,
            "cases": [first.metrics, repeated.metrics, before.metrics, cancelled.metrics, after.metrics, alternate.metrics],
            "limitations": ["not an SDK or physical-audio test", "no claimed checkpoint parity or human-quality pass",
                             "fixed plain-word development panel; normalization coverage not qualified",
                             "private state-pipeline completion correction; IO pipeline not changed",
                             "memory samples are not an instrumented peak or energy measurement"]
        ], to: output.appendingPathComponent("result.json"))
    }
}

#if os(macOS)
@main struct Probe {
    static func main() async {
        do {
            let args = CommandLine.arguments
            guard args.count == 4 else { throw ProbeError.refused("usage: cache output cpu|gpu|ane") }
            try await ReferenceProbe.run(cache: URL(fileURLWithPath: args[1]),
                                         output: URL(fileURLWithPath: args[2]), compute: args[3])
        } catch {
            log("REFERENCE_FAILED \(error)")
            exit(1)
        }
    }
}
#elseif os(iOS)
@main class ProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let controller = UIViewController()
        controller.view.backgroundColor = .systemBackground
        let label = UILabel(frame: CGRect(x: 20, y: 90, width: 350, height: 200))
        label.numberOfLines = 0
        label.text = "AII Voice private Core ML reference\nRecorded-output test; microphone is off."
        controller.view.addSubview(label)
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = controller
        window.makeKeyAndVisible()
        self.window = window
        Task {
            let env = ProcessInfo.processInfo.environment
            let name = env["AII_POCKET_RUN_ID"] ?? "missing"
            let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            let output = docs.appendingPathComponent(name)
            do {
                guard name.count <= 64, name != "missing", name.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-") }) else {
                    throw ProbeError.refused("explicit safe run id required")
                }
                if env["AII_POCKET_PROFILE_PREPARE"] == "1" {
                    // Instrumented runs only: allow attaching the hardware
                    // profiler before model load; not part of latency timing.
                    try await Task.sleep(for: .seconds(15))
                }
                if env["AII_POCKET_DIAGNOSTICS"] == "1" {
                    setenv("AII_POCKET_TRACE_ROOT", output.appendingPathComponent("intermediates").path, 1)
                }
                try await ReferenceProbe.run(cache: docs.appendingPathComponent("cache"),
                    output: output, compute: env["AII_POCKET_COMPUTE"] ?? "invalid")
                label.text = "Reference completed. Component evidence only."
                log("PHONE_REFERENCE_COMPLETE \(name)")
            } catch {
                label.text = "Reference failed: \(error)"
                log("PHONE_REFERENCE_FAILED \(error)")
                if FileManager.default.fileExists(atPath: output.path) {
                    try? writeJSON(["status": "failed", "error": String(describing: error)],
                                   to: output.appendingPathComponent("failure.json"))
                }
            }
        }
        return true
    }
}
#endif
