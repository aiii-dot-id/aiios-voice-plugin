import XCTest
import AudioRing

final class AudioRingTests: XCTestCase {
    func testConcurrentProducerConsumerPreservesEveryFrame() throws {
        let q = try XCTUnwrap(vf_queue_create()); defer { vf_queue_destroy(q) }
        let finished = expectation(description: "producer completed")
        let count = 10000
        let deadline = Date().addingTimeInterval(10)
        DispatchQueue.global(qos: .userInitiated).async {
            for n in 0..<count {
                while vf_queue_depth(q) >= 64 && Date() < deadline { Thread.sleep(forTimeInterval: 0.00001) }
                if Date() >= deadline { break }
                let data = [Float(n)]
                XCTAssertEqual(data.withUnsafeBufferPointer { vf_queue_push(q, $0.baseAddress, 1, UInt64(n), Double(n), 3) }, 1)
            }
            finished.fulfill()
        }
        var frame = VFFrame(); var received = 0
        while received < count && Date() < deadline {
            if vf_queue_pop(q, &frame) == 0 { Thread.sleep(forTimeInterval: 0.00001); continue }
            XCTAssertEqual(frame.host_time, UInt64(received))
            withUnsafePointer(to: frame) { XCTAssertEqual(vf_frame_samples($0)[0], Float(received)) }
            received += 1
        }
        wait(for: [finished], timeout: 11)
        XCTAssertEqual(received, count); XCTAssertEqual(vf_queue_drops(q), 0)
    }
    func testBoundedFIFOAndOverflowEvidence() throws {
        let q = try XCTUnwrap(vf_queue_create()); defer { vf_queue_destroy(q) }
        var frame = VFFrame()
        for n in 0..<64 {
            let x = [Float(n), Float(n)+0.5]
            XCTAssertEqual(x.withUnsafeBufferPointer {vf_queue_push(q, $0.baseAddress, 2, UInt64(n), Double(n*2), 3)}, 1)
        }
        let x: [Float] = [1]
        XCTAssertEqual(x.withUnsafeBufferPointer {vf_queue_push(q, $0.baseAddress, 1, 0, 0, 3)}, 0)
        XCTAssertEqual(vf_queue_drops(q), 1)
        for n in 0..<64 {
            XCTAssertEqual(vf_queue_pop(q, &frame), 1)
            XCTAssertEqual(frame.count, 2); XCTAssertEqual(frame.host_time, UInt64(n))
            withUnsafePointer(to: frame) { pointer in
                XCTAssertEqual(vf_frame_samples(pointer)[0], Float(n))
                XCTAssertEqual(vf_frame_samples(pointer)[1], Float(n)+0.5)
            }
        }
        XCTAssertEqual(vf_queue_pop(q, &frame), 0)
        XCTAssertEqual(vf_queue_depth(q), 0)
    }
    func testWrapAndOversizeRefusal() throws {
        let q = try XCTUnwrap(vf_queue_create()); defer { vf_queue_destroy(q) }
        var frame = VFFrame(); let x: [Float] = [0.25]
        for _ in 0..<512 {
            XCTAssertEqual(x.withUnsafeBufferPointer {vf_queue_push(q, $0.baseAddress, 1, 9, 1, 3)}, 1)
            XCTAssertEqual(vf_queue_pop(q, &frame), 1)
        }
        XCTAssertEqual(x.withUnsafeBufferPointer {vf_queue_push(q, $0.baseAddress, 8193, 0, 0, 0)}, 0)
        XCTAssertEqual(vf_queue_drops(q), 1)
    }
}
