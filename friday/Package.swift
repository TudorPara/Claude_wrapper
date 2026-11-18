// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "Friday",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "Friday",
            path: "Sources/Friday",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "FridayTests",
            dependencies: ["Friday"],
            path: "Tests/FridayTests",
            resources: [.copy("Fixtures")],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
