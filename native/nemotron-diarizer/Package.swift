// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "EchoNoteNemotron",
    platforms: [.macOS(.v14)],
    products: [
        .executable(
            name: "echonote-nemotron-diarizer",
            targets: ["NemotronDiarizer"]
        )
    ],
    dependencies: [
        .package(
            url: "https://github.com/FluidInference/FluidAudio.git",
            exact: "0.17.1"
        )
    ],
    targets: [
        .executableTarget(
            name: "NemotronDiarizer",
            dependencies: [
                .product(name: "FluidAudio", package: "FluidAudio")
            ]
        )
    ]
)
