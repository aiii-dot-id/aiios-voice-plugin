// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AIIAudioHost",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "aii-audio-host", targets: ["AIIAudioHost"])],
    targets: [
        .target(name: "AudioRing", publicHeadersPath: "include"),
        .executableTarget(name: "AIIAudioHost", dependencies: ["AudioRing"]),
        .testTarget(name: "AudioRingTests", dependencies: ["AudioRing"]),
    ]
)
