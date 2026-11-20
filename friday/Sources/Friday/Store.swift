import Foundation
import Observation
import SwiftUI

/// A command FRIDAY started, with its live output.
@MainActor
@Observable
final class Job: Identifiable {
    let id = UUID()
    let title: String
    let project: String
    let arguments: [String]
    let startedAt = Date()
    var ticketId: String?
    var log = ""
    var running = true
    var exitCode: Int32?
    @ObservationIgnored var process: Process?

    init(title: String, project: String, ticketId: String?, arguments: [String]) {
        self.title = title
        self.project = project
        self.ticketId = ticketId
        self.arguments = arguments
    }

    var failed: Bool { !running && exitCode != 0 }
    var lastLine: String { log.split(separator: "\n").last.map(String.init) ?? "" }
}

enum SidebarItem: Hashable {
    case project(String)
    case activity, usage, toolkit, settings
}

enum TicketTab: String, CaseIterable, Identifiable {
    case overview = "Overview"
    case plan = "Plan"
    case work = "Work"
    case review = "Review"
    case activity = "Activity"
    case cost = "Cost"
    var id: String { rawValue }
}

enum SheetRoute: Identifiable {
    case newTicket(project: String?)
    case newProject
    case addProject
    case projectSettings(String)
    case conventions(String)

    var id: String {
        switch self {
        case .newTicket(let project): "new-ticket-\(project ?? "")"
        case .newProject: "new-project"
        case .addProject: "add-project"
        case .projectSettings(let name): "settings-\(name)"
        case .conventions(let name): "conventions-\(name)"
        }
    }
}

/// Everything FRIDAY knows, read from `cw ... --json`, and every command it runs.
@MainActor
@Observable
final class Store {
    var config: CWConfig?
    @ObservationIgnored private var rawConfig: Any?
    var configLoaded = false
    var projectsInfo: [ProjectInfo] = []
    var tickets: [TicketSummary] = []
    var toolkit: ToolkitInventory?
    var usage: [UsageRow] = []
    var doctor: [DoctorRow] = []
    var jobs: [Job] = []
    var lastError: String?
    let voice = Voice()

    var sidebar: SidebarItem? = nil
    var selectedTicket: String?
    var ticketTab: TicketTab = .overview
    var showAgent: String?
    var sheet: SheetRoute?

    var cwExecutable = UserDefaults.standard.string(forKey: "cwExecutable") ?? "cw" {
        didSet { UserDefaults.standard.set(cwExecutable, forKey: "cwExecutable") }
    }
    var cwHome = UserDefaults.standard.string(forKey: "cwHome") ?? "" {
        didSet { UserDefaults.standard.set(cwHome, forKey: "cwHome") }
    }

    @ObservationIgnored let fixtures: URL?
    @ObservationIgnored private var timer: Timer?
    @ObservationIgnored private var tick = 0

    private static let ticketIdPattern = try! NSRegularExpression(pattern: "(\\d{6}-[a-z0-9-]+-[0-9a-f]{4})")
    private static let pollSeconds = 3.0
    private static let projectPollEvery = 3

    init(fixtures: URL? = nil) {
        self.fixtures = fixtures
    }

    var cw: CW { CW(executable: cwExecutable, home: cwHome) }
    var needsSetup: Bool { configLoaded && config == nil }
    var selectedProject: String? {
        if case .project(let name) = sidebar { return name }
        return nil
    }
    var runningJobs: [Job] { jobs.filter(\.running) }
    var doctorProblems: [DoctorRow] { doctor.filter { !$0.isOK } }

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
        Task {
            await refreshAll()
            if sidebar == nil, let first = config?.projects.first { sidebar = .project(first.name) }
            await refreshDoctor()
        }
        guard fixtures == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: Self.pollSeconds, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.poll() }
        }
    }

    private func poll() async {
        guard config != nil else { return }
        tick += 1
        await refreshTickets()
        if tick % Self.projectPollEvery == 0 { await refreshProjects() }
    }

    func refreshAll() async {
        await refreshConfig()
        guard config != nil else { return }
        await refreshTickets()
        await refreshProjects()
        await refreshToolkit()
        await refreshUsage()
    }

    private func load<T: Decodable>(_ type: T.Type, _ arguments: [String], fixture: String, decoder: JSONDecoder = JSONDecoder()) async throws -> T {
        if let fixtures {
            return try decoder.decode(T.self, from: Data(contentsOf: fixtures.appendingPathComponent(fixture)))
        }
        return try await cw.decode(T.self, arguments, decoder: decoder)
    }

    private func loadData(_ arguments: [String], fixture: String) async throws -> Data {
        if let fixtures {
            return try Data(contentsOf: fixtures.appendingPathComponent(fixture))
        }
        return try await cw.output(arguments)
    }

    func refreshConfig() async {
        do {
            let data = try await loadData(["config", "--json"], fixture: "config.json")
            config = try JSONDecoder().decode(CWConfig.self, from: data)
            rawConfig = try JSONSerialization.jsonObject(with: data)
            lastError = nil
        } catch {
            config = nil
        }
        configLoaded = true
    }

    func refreshProjects() async {
        if let fresh = try? await load([ProjectInfo].self, ["project", "list"], fixture: "projects.json"), fresh != projectsInfo {
            projectsInfo = fresh
        }
    }

    func refreshTickets() async {
        let arguments = ["status", "--json", "--include-archived"]
        if let fresh = try? await load([TicketSummary].self, arguments, fixture: "status.json", decoder: snakeCaseDecoder), fresh != tickets {
            tickets = fresh
        }
    }

    func refreshToolkit() async {
        toolkit = try? await load(ToolkitInventory.self, ["toolkit", "--json"], fixture: "toolkit.json")
    }

    func refreshUsage() async {
        usage = (try? await load([UsageRow].self, ["stats", "--json"], fixture: "stats.json")) ?? []
    }

    func refreshDoctor() async {
        doctor = (try? await load([DoctorRow].self, ["doctor", "--json"], fixture: "doctor.json")) ?? []
    }

    func ticket(_ id: String) async -> Ticket? {
        try? await load(Ticket.self, ["show", id, "--json"], fixture: "show-\(id).json", decoder: snakeCaseDecoder)
    }

    func events(_ id: String) async -> [AgentEvent] {
        (try? await load([AgentEvent].self, ["events", id, "--json"], fixture: "events-\(id).json", decoder: snakeCaseDecoder)) ?? []
    }

    func tickets(in project: String) -> [TicketSummary] {
        tickets.filter { $0.project == project }
    }

    func summary(_ id: String) -> TicketSummary? {
        tickets.first { $0.id == id }
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

    func isRunning(_ ticketId: String) -> Bool {
        summary(ticketId)?.running == true || job(for: ticketId)?.running == true
    }

    func cost(of project: String) -> Double {
        usage.filter { $0.project == project }.map(\.costUsd).reduce(0, +)
    }

    func select(project: String, ticket: String? = nil) {
        sidebar = .project(project)
        selectedTicket = ticket
    }

    func save(_ newConfig: CWConfig) async {
        do {
            let edited = try JSONSerialization.jsonObject(with: JSONEncoder().encode(newConfig))
            let merged = mergedConfig(rawConfig, edited)
            let body = try JSONSerialization.data(withJSONObject: merged, options: [.prettyPrinted, .sortedKeys])
            _ = try await cw.output(["config", "set"], stdin: body)
            lastError = nil
            await refreshConfig()
            await refreshToolkit()
        } catch {
            lastError = "Not saved: \(error.localizedDescription)"
        }
    }

    /// Runs a cw command as a job whose output streams into the app; a new ticket's id is picked up from it.
    @discardableResult
    func run(_ title: String, project: String, ticketId: String?, _ arguments: [String], using runner: CW? = nil) -> Job {
        let job = Job(title: title, project: project, ticketId: ticketId, arguments: arguments)
        jobs.append(job)
        guard fixtures == nil else {
            job.running = false
            job.exitCode = 0
            return job
        }
        do {
            job.process = try (runner ?? cw).stream(
                arguments,
                onOutput: { text in
                    Task { @MainActor in self.append(text, to: job) }
                },
                onExit: { code in
                    Task { @MainActor in
                        job.running = false
                        job.exitCode = code
                        await self.refreshAll()
                        self.announce(job)
                    }
                })
        } catch {
            job.log = "Could not start: \(error.localizedDescription)"
            job.running = false
            job.exitCode = -1
        }
        return job
    }

    private func append(_ text: String, to job: Job) {
        job.log += text
        guard job.ticketId == nil else { return }
        let range = NSRange(job.log.startIndex..., in: job.log)
        guard let match = Self.ticketIdPattern.firstMatch(in: job.log, range: range),
            let found = Range(match.range(at: 1), in: job.log)
        else { return }
        job.ticketId = String(job.log[found])
        if selectedProject == job.project { selectedTicket = job.ticketId }
        Task { await refreshTickets() }
    }

    func stop(_ job: Job) {
        job.process?.terminate()
    }

    /// Says where the job's ticket stands when a step ends, if "Speak replies" is on.
    private func announce(_ job: Job) {
        guard UserDefaults.standard.bool(forKey: Preference.speakReplies) else { return }
        if let id = job.ticketId, let summary = summary(id) {
            voice.speak("\(summary.title). \(summary.status.label).")
        } else {
            voice.speak(job.exitCode == 0 ? "\(job.title) finished." : "\(job.title) failed.")
        }
    }
}

/// The open ticket in full, with its agent events, refreshed often while it runs.
@MainActor
@Observable
final class TicketFeed {
    let ticketId: String
    var ticket: Ticket?
    var events: [AgentEvent] = []
    var loaded = false

    private static let fastSeconds: UInt64 = 2
    private static let slowSeconds: UInt64 = 8

    init(ticketId: String) {
        self.ticketId = ticketId
    }

    func refresh(using store: Store) async {
        async let ticket = store.ticket(ticketId)
        async let events = store.events(ticketId)
        let (freshTicket, freshEvents) = await (ticket, events)
        if let freshTicket, freshTicket != self.ticket { self.ticket = freshTicket }
        if freshEvents != self.events { self.events = freshEvents }
        loaded = true
    }

    /// Keeps the ticket fresh until the view goes away: every 2 seconds while it runs, every 8 otherwise.
    func follow(using store: Store) async {
        while !Task.isCancelled {
            await refresh(using: store)
            if store.fixtures != nil { return }
            let seconds = store.isRunning(ticketId) ? Self.fastSeconds : Self.slowSeconds
            try? await Task.sleep(nanoseconds: seconds * 1_000_000_000)
        }
    }
}
