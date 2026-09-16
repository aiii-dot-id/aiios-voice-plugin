// Private implementation of the existing native TTS ABI. This is not an SDK
// extension. C++ owns serialization/lifetime; cancel never waits for inference.
import CoreML
import FluidAudio
import Foundation
import Synchronization

private struct BridgeFailure: Error, Sendable { let message: String }
private func fail(_ text: String) -> BridgeFailure { BridgeFailure(message: text) }

private actor PocketWorker {
    let manager: PocketTtsManager
    var generation: UInt64 = 0
    var cancelledThrough: UInt64 = 0
    var session: PocketTtsSession?
    var iterator: AsyncThrowingStream<PocketTtsSynthesizer.AudioFrame, Error>.Iterator?
    var deliveredFrames = 0

    init(cache: URL) {
        manager = PocketTtsManager(directory: cache, placement: .aneState,
            computeUnits: PocketTtsComputeUnits(conditioner: .cpuAndGPU, flowLM: .cpuAndGPU, mimiDecoder: .cpuOnly))
    }
    func initialize() async throws { try await manager.initialize() }
    func start(_ id: UInt64, text: String, voice: String, temperature: Float, seed: UInt32, limit: Int) async throws {
        guard session == nil, id > generation, id > cancelledThrough else { throw CancellationError() }
        generation = id
        let created = try await manager.makeSession(voice: voice, temperature: temperature, seed: UInt64(seed))
        try await created.enablePullOutput(frameLimit: limit)
        guard id > cancelledThrough else { await created.cancel(); throw CancellationError() }
        session = created
        iterator = created.frames.makeAsyncIterator()
        deliveredFrames = 0
        created.enqueue(text)
        created.finish()
    }
    func next(_ id: UInt64) async throws -> [Float] {
        guard id == generation, id > cancelledThrough, let session, var current = iterator else {
            throw CancellationError()
        }
        iterator = nil // One in-flight pull; control may re-enter, another pull may not.
        try await session.requestFrame()
        let frame = try await current.next(isolation: self)
        iterator = current
        guard id > cancelledThrough else { throw CancellationError() }
        guard let frame else {
            let completed = await session.chunkCompletions
            guard !completed.isEmpty,
                  completed.reduce(0, { $0 + $1.emittedFrames }) == deliveredFrames,
                  completed.allSatisfy({ $0.emittedFrames == $0.eosStep + $0.tailFrames }) else {
                throw fail("natural completion lost its exact audio tail")
            }
            return []
        }
        guard frame.samples.count == 1920, frame.samples.allSatisfy(\.isFinite), deliveredFrames < 750 else {
            await session.cancel()
            throw fail("invalid PCM or native segment audio bound exceeded")
        }
        deliveredFrames += 1
        return frame.samples
    }
    func cancel(_ id: UInt64) async {
        cancelledThrough = max(cancelledThrough, id)
        if id >= generation { await session?.cancel() }
    }
    func reset(_ id: UInt64) async throws {
        guard id == generation else { throw fail("stale reset") }
        await session?.cancel()
        session = nil
        iterator = nil
    }
}

private struct Flags {
    var generation: UInt64 = 0
    var cancelled: UInt64 = 0
    var computing = false
    var active = false
    var faulted = false
    var voice = "alba"
    var temperature: Float = 0.3
}
private final class PocketHandle: Sendable {
    let worker: PocketWorker
    let flags = Mutex(Flags())
    let voices: Set<String>
    init(cache: URL) throws {
        let constants = cache.appendingPathComponent("Models/pocket-tts/v2.1/english/constants_bin")
        voices = Set(try FileManager.default.contentsOfDirectory(atPath: constants.path)
            .filter { $0.hasSuffix(".safetensors") }.map { String($0.dropLast(".safetensors".count)) })
        guard voices.contains("alba") else { throw fail("bound default voice missing") }
        worker = PocketWorker(cache: cache)
    }
}

// Only initialization/the dedicated native inference owner may wait here.
// The cancel and state exports deliberately do not enter this bridge.
private func synchronous<T: Sendable>(_ action: @escaping @Sendable () async throws -> T) throws -> T {
    guard !Thread.isMainThread else { throw fail("native inference must not block the UI thread") }
    let box = Mutex<Result<T, Error>?>(nil)
    let ready = DispatchSemaphore(value: 0)
    Task {
        let result: Result<T, Error>
        do { result = .success(try await action()) } catch { result = .failure(error) }
        box.withLock { $0 = result }
        ready.signal()
    }
    ready.wait()
    guard let result = box.withLock({ $0 }) else { throw fail("missing native inference result") }
    return try result.get()
}
private func handle(_ raw: UnsafeMutableRawPointer?) throws -> PocketHandle {
    guard let raw else { throw fail("missing native handle") }
    return Unmanaged<PocketHandle>.fromOpaque(raw).takeUnretainedValue()
}
private func string(_ raw: UnsafePointer<CChar>?, limit: Int) throws -> String {
    guard let raw else { throw fail("missing argument") }
    let n = strnlen(raw, limit + 1)
    guard n > 0, n <= limit, let text = String(bytes: UnsafeRawBufferPointer(start: raw, count: n), encoding: .utf8) else {
        throw fail("argument length or UTF-8 refused")
    }
    return text
}
private func message(_ text: String, _ error: UnsafeMutablePointer<CChar>?, _ capacity: Int) {
    guard let error, capacity > 0 else { return }
    let bytes = Array(text.utf8.prefix(capacity - 1))
    for (i, byte) in bytes.enumerated() { error[i] = CChar(bitPattern: byte) }
    error[bytes.count] = 0
}
private func verdict(_ error: Error, output: UnsafeMutablePointer<CChar>?, capacity: Int) -> Int32 {
    if error is CancellationError { return -2 }
    message(String(describing: error), output, capacity)
    return -1
}

@_cdecl("nv_create_bound")
public func createBound(_ assets: UnsafePointer<CChar>?, _ config: UnsafePointer<CChar>?, _ backend: UnsafePointer<CChar>?,
                        _ threads: Int32, _ error: UnsafeMutablePointer<CChar>?, _ capacity: Int) -> UnsafeMutableRawPointer? {
    do {
        guard try string(backend, limit: 32) == "coreml-gpu", threads == 4 else {
            throw fail("explicit Core ML GPU candidate required; no fallback")
        }
        let path = try string(assets, limit: 4096)
        let configPath = try string(config, limit: 4096)
        // The app verifies this manifest and every immutable model file before
        // the native owner starts. Require that exact declared root, not a URL.
        guard path.hasPrefix("/"), configPath == URL(fileURLWithPath: path).appendingPathComponent("binding.json").path,
              FileManager.default.fileExists(atPath: configPath) else { throw fail("explicit bound Core ML inventory required") }
        let owner = try PocketHandle(cache: URL(fileURLWithPath: path))
        try synchronous { try await owner.worker.initialize() }
        return Unmanaged.passRetained(owner).toOpaque()
    } catch let caught { message(String(describing: caught), error, capacity); return nil }
}

@_cdecl("nv_configure_voice")
public func configureVoice(_ raw: UnsafeMutableRawPointer?, _ voice: UnsafePointer<CChar>?, _ temperature: Float,
                           _ error: UnsafeMutablePointer<CChar>?, _ capacity: Int) -> Int32 {
    do {
        let h = try handle(raw), selected = try string(voice, limit: 64)
        guard h.voices.contains(selected), temperature.isFinite, (0...1).contains(temperature) else { throw fail("voice or temperature refused") }
        return h.flags.withLock { f in
            guard !f.computing, !f.active else { return -3 }
            guard !f.faulted else { return -1 }
            f.voice = selected; f.temperature = temperature
            return 0
        }
    } catch let caught { return verdict(caught, output: error, capacity: capacity) }
}

@_cdecl("nv_start")
public func start(_ raw: UnsafeMutableRawPointer?, _ id: UInt64, _ text: UnsafePointer<CChar>?, _ seed: UInt32,
                  _ limit: Int32, _ noise: UnsafePointer<CChar>?, _ error: UnsafeMutablePointer<CChar>?, _ capacity: Int) -> Int32 {
    do {
        let h = try handle(raw), words = try string(text, limit: 2048)
        guard limit >= 1, limit <= 750, noise == nil || noise?.pointee == 0 else { throw fail("frame bound or unsupported explicit noise file") }
        let config: (String, Float) = try h.flags.withLock { f in
            guard !f.computing, !f.active, !f.faulted, id > f.generation else { throw fail("generation ownership refused") }
            guard id > f.cancelled else { throw CancellationError() }
            f.generation = id; f.computing = true; f.active = true
            return (f.voice, f.temperature)
        }
        defer { h.flags.withLock { $0.computing = false } }
        do {
            try synchronous { try await h.worker.start(id, text: words, voice: config.0, temperature: config.1, seed: seed, limit: Int(limit)) }
        } catch {
            if !(error is CancellationError) { h.flags.withLock { $0.faulted = true; $0.active = false } }
            throw error
        }
        if h.flags.withLock({ $0.cancelled >= id }) { return -2 }
        return 0
    } catch let caught { return verdict(caught, output: error, capacity: capacity) }
}

@_cdecl("nv_next")
public func next(_ raw: UnsafeMutableRawPointer?, _ id: UInt64, _ pcm: UnsafeMutablePointer<Float>?, _ pcmCapacity: Int,
                 _ samples: UnsafeMutablePointer<Int>?, _ error: UnsafeMutablePointer<CChar>?, _ capacity: Int) -> Int32 {
    samples?.pointee = 0
    do {
        guard let pcm, let samples, pcmCapacity >= 1920, pcmCapacity <= 120000 else { throw fail("invalid native PCM buffer") }
        let h = try handle(raw)
        let active = try h.flags.withLock { f in
            guard id == f.generation, !f.computing, !f.faulted else { throw fail("stale or busy inference") }
            guard f.cancelled < id else { throw CancellationError() }
            if !f.active { return false }
            f.computing = true; return true
        }
        guard active else { return 0 }
        defer { h.flags.withLock { $0.computing = false } }
        let audio: [Float]
        do { audio = try synchronous { try await h.worker.next(id) } }
        catch {
            if !(error is CancellationError) { h.flags.withLock { $0.faulted = true; $0.active = false } }
            throw error
        }
        return h.flags.withLock { f in
            guard f.cancelled < id else { return -2 }
            if audio.isEmpty { f.active = false; return 0 }
            audio.withUnsafeBufferPointer { buffer in
                if let base = buffer.baseAddress { pcm.update(from: base, count: audio.count) }
            }
            samples.pointee = audio.count
            return 1
        }
    } catch let caught { return verdict(caught, output: error, capacity: capacity) }
}

@_cdecl("nv_cancel")
public func cancel(_ raw: UnsafeMutableRawPointer?, _ id: UInt64) -> Int32 {
    guard let h = try? handle(raw), id > 0 else { return -1 }
    h.flags.withLock { $0.cancelled = max($0.cancelled, id) }
    Task { await h.worker.cancel(id) }
    return 0 // No inference wait or ownership lock.
}

@_cdecl("nv_reset")
public func reset(_ raw: UnsafeMutableRawPointer?, _ id: UInt64, _ error: UnsafeMutablePointer<CChar>?, _ capacity: Int) -> Int32 {
    do {
        let h = try handle(raw)
        try h.flags.withLock { f in
            guard id == f.generation, !f.computing else { throw fail("stale or busy reset") }
            f.computing = true
        }
        defer { h.flags.withLock { $0.computing = false } }
        try synchronous { try await h.worker.reset(id) }
        h.flags.withLock { $0.active = false }
        return 0
    } catch let caught { return verdict(caught, output: error, capacity: capacity) }
}

@_cdecl("nv_state")
public func state(_ raw: UnsafeMutableRawPointer?) -> UInt32 {
    guard let h = try? handle(raw) else { return 8 }
    return h.flags.withLock { f in
        (f.computing ? 1 : 0) | (f.active ? 2 : 0) | (f.generation > 0 && f.cancelled >= f.generation ? 4 : 0) | (f.faulted ? 8 : 0)
    }
}

@_cdecl("nv_destroy")
public func destroy(_ raw: UnsafeMutableRawPointer?) -> Int32 {
    guard let raw else { return 0 }
    guard let h = try? handle(raw), h.flags.withLock({ !$0.computing && !$0.active }) else { return -3 }
    Unmanaged<PocketHandle>.fromOpaque(raw).release()
    return 0
}
