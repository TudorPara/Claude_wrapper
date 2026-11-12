import Foundation

/// Runs the `cw` command line through a login shell, so it sees the same PATH (claude, gh, uv) as the terminal.
struct CW {
    var executable: String
    var home: String

    private func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private func makeProcess(_ arguments: [String]) -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        let command = ([executable] + arguments).map(shellQuote).joined(separator: " ")
        process.arguments = ["-lc", command]
        var environment = ProcessInfo.processInfo.environment
        if !home.isEmpty { environment["CW_HOME"] = (home as NSString).expandingTildeInPath }
        process.environment = environment
        return process
    }

    struct Failure: LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }

    /// Runs a short command and returns stdout; throws with stderr on failure.
    func output(_ arguments: [String], stdin: Data? = nil) async throws -> Data {
        let process = makeProcess(arguments)
        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err
        let input = Pipe()
        process.standardInput = input
        return try await withCheckedThrowingContinuation { continuation in
            process.terminationHandler = { finished in
                let data = out.fileHandleForReading.readDataToEndOfFile()
                let errors = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                if finished.terminationStatus == 0 {
                    continuation.resume(returning: data)
                } else {
                    continuation.resume(throwing: Failure(message: errors.isEmpty ? "cw exited with \(finished.terminationStatus)" : errors))
                }
            }
            do {
                try process.run()
                if let stdin { input.fileHandleForWriting.write(stdin) }
                try? input.fileHandleForWriting.close()
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }

    func decode<T: Decodable>(_ type: T.Type, _ arguments: [String]) async throws -> T {
        let data = try await output(arguments)
        return try JSONDecoder().decode(T.self, from: data)
    }

    /// Starts a long command and streams its combined output line by line.
    func stream(_ arguments: [String], onOutput: @escaping (String) -> Void, onExit: @escaping (Int32) -> Void) throws -> Process {
        let process = makeProcess(arguments)
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        process.standardInput = FileHandle.nullDevice
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            onOutput(text)
        }
        process.terminationHandler = { finished in
            pipe.fileHandleForReading.readabilityHandler = nil
            onExit(finished.terminationStatus)
        }
        try process.run()
        return process
    }
}
