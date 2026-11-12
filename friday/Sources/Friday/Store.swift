import Foundation
import Observation
import SwiftUI

/// A command FRIDAY started, with its live output.
@Observable
final class Job: Identifiable {
    let id = UUID()
    let title: String
    let project: String
    let startedAt = Date()
    var ticketId: String?
    var log = ""
    var running = true
    var exitCode: Int32?
    var process: Process?

    init(title: String, project: String, ticketId: String?) {
        self.title = title
        self.project = project
        self.ticketId = ticketId
    }
}

enum Rail: String, CaseIterable, Identifiable {
    case projects = "Projects"
    case activity = "Activity"
    case toolkit = "Toolkit"
    case settings = "Settings"
    var id: String { rawValue }

    var icon: String {
        switch self {
        case .projects: "folder"
        case .activity: "waveform.path.ecg"
        case .toolkit: "square.stack.3d.up"
        case .settings: "gearshape"
        }
    }
}

enum TopTab: String, CaseIterable, Identifiable {
    case dashboard = "Dashboard"
    case usage = "Usage"
    var id: String { rawValue }
}

@MainActor
@Observable
final class Store {
    var config: CWConfig?
    var configLoaded = false
    var projectsInfo: [ProjectInfo] = []
    var tickets: [TicketSummary] = []
    var toolkit: ToolkitInventory?
    var usage: [UsageRow] = []
    var jobs: [Job] = []
    var lastError: String?

    var rail: Rail = .projects
    var topTab: TopTab = .dashboard
    var selectedProject: String?
    var selectedTicket: String?
    var showProjectSettings = false

    var cwExecutable = UserDefaults.standard.string(forKey: "cwExecutable") ?? "cw" {
        didSet { UserDefaults.standard.set(cwExecutable, forKey: "cwExecutable") }
    }
    var cwHome = UserDefaults.standard.string(forKey: "cwHome") ?? "" {
        didSet { UserDefaults.standard.set(cwHome, forKey: "cwHome") }
    }
    @ObservationIgnored private var timer: Timer?
    @ObservationIgnored private var tick = 0

    private static let ticketIdPattern = try! NSRegularExpression(pattern: "Ticket (\\d{6}-[a-z0-9-]+-[0-9a-f]{4})")

    var cw: CW { CW(executable: cwExecutable, home: cwHome) }
    var needsSetup: Bool { configLoaded && config == nil }

    /// The repository FRIDAY was built from, found by walking up from the executable.
    var repositoryRoot: String? {
        var url = Bundle.main.executableURL?.deletingLastPathComponent()
        while let current = url, current.path != "/" {
            if FileManager.default.fileExists(atPath: current.appendingPathComponent("backend/pyproject.toml").path) {
                return current.path
            }
            url = current.deletingLastPathComponent()
        }
        return nil
    }

    func start() {
        Task { await refreshAll() }
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.poll() }
        }
    }

    private func poll() async {
        guard config != nil else { return }
        tick += 1
        await refreshTickets()
        if tick % 3 == 0 { await refreshProjects() }
    }

    func refreshAll() async {
        await refreshConfig()
        guard config != nil else { return }
        await refreshTickets()
        await refreshProjects()
        await refreshToolkit()
        await refreshUsage()
    }

    func refreshConfig() async {
        do {
            config = try await cw.decode(CWConfig.self, ["config", "--json"])
            lastError = nil
        } catch {
            config = nil
        }
        configLoaded = true
    }

    func refreshProjects() async {
        if let fresh = try? await cw.decode([ProjectInfo].self, ["project", "list"]), fresh != projectsInfo {
            projectsInfo = fresh
        }
    }

    func refreshTickets() async {
        if let fresh = try? await cw.decode([TicketSummary].self, ["status", "--json", "--include-archived"]), fresh != tickets {
            tickets = fresh
        }
    }

    func refreshToolkit() async {
        toolkit = try? await cw.decode(ToolkitInventory.self, ["toolkit", "--json"])
    }

    func refreshUsage() async {
        usage = (try? await cw.decode([UsageRow].self, ["stats", "--json"])) ?? []
    }

    func ticket(_ id: String) async -> Ticket? {
        try? await cw.decode(Ticket.self, ["show", id, "--json"])
    }

    func tickets(in project: String) -> [TicketSummary] {
        tickets.filter { $0.project == project }
    }

    func info(_ project: String) -> ProjectInfo? {
        projectsInfo.first { $0.name == project }
    }

    func project(_ name: String) -> Project? {
        config?.projects.first { $0.name == name }
    }

    func job(for ticketId: String) -> Job? {
        jobs.last { $0.ticketId == ticketId }
    }

    func save(_ newConfig: CWConfig) async {
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            _ = try await cw.output(["config", "set"], stdin: try encoder.encode(newConfig))
            lastError = nil
            await refreshConfig()
            await refreshToolkit()
        } catch {
            lastError = "Not saved: \(error.localizedDescription)"
        }
    }

    /// Runs a cw command as a job whose output streams into the app.
    @discardableResult
    func run(_ title: String, project: String, ticketId: String?, _ arguments: [String], using runner: CW? = nil) -> Job {
        let job = Job(title: title, project: project, ticketId: ticketId)
        jobs.append(job)
        do {
            job.process = try (runner ?? cw).stream(
                arguments,
                onOutput: { text in
                    Task { @MainActor in
                        job.log += text
                        if job.ticketId == nil,
                            let match = Self.ticketIdPattern.firstMatch(in: job.log, range: NSRange(job.log.startIndex..., in: job.log)),
                            let range = Range(match.range(at: 1), in: job.log)
                        {
                            job.ticketId = String(job.log[range])
                            if self.selectedProject == job.project { self.selectedTicket = job.ticketId }
                        }
                    }
                },
                onExit: { code in
                    Task { @MainActor in
                        job.running = false
                        job.exitCode = code
                        await self.refreshAll()
                    }
                })
        } catch {
            job.log = "Could not start: \(error.localizedDescription)"
            job.running = false
        }
        return job
    }

    func stop(_ job: Job) {
        job.process?.terminate()
    }
}
