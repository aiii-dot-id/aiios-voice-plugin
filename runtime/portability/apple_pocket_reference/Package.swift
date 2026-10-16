// swift-tools-version: 6.3
import PackageDescription

let package = Package(
    name: "AiiPocketCoreMLReference",
    platforms: [.macOS(.v15), .iOS(.v18)],
    dependencies: [
        .package(name: "FluidAudio", path: "../../../artifacts/sources/fluidaudio-stage-isolation-r1", traits: [])
    ],
    targets: [
        .executableTarget(name: "pocket-coreml-reference", dependencies: [.product(name: "FluidAudio", package: "FluidAudio")])
    ]
)
