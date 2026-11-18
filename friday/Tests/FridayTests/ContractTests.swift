import Foundation
import Testing

@testable import Friday

private func fixture(_ name: String) throws -> Data {
    let url = try #require(Bundle.module.url(forResource: "Fixtures/\(name)", withExtension: nil))
    return try Data(contentsOf: url)
}

private func ticket(_ id: String) throws -> Ticket {
    try snakeCaseDecoder.decode(Ticket.self, from: fixture("show-\(id).json"))
}

private func events(_ id: String) throws -> [AgentEvent] {
    try snakeCaseDecoder.decode([AgentEvent].self, from: fixture("events-\(id).json"))
}

private let running = "261118-csv-import-a1b2"
private let review = "261118-monthly-report-c3d4"
private let approval = "261118-budget-alerts-e5f6"
private let blocked = "261118-cookie-auth-i9j0"

@Test func everyCommandFridayReadsDecodes() throws {
    let config = try JSONDecoder().decode(CWConfig.self, from: fixture("config.json"))
    #expect(config.projects.map(\.name) == ["expense-tracker"])
    #expect(config.defaults.limits.maxPullRequestFixRounds == 3)
    #expect(try JSONDecoder().decode([ProjectInfo].self, from: fixture("projects.json")).count == 1)
    #expect(try snakeCaseDecoder.decode([TicketSummary].self, from: fixture("status.json")).count == 6)
    #expect(try !JSONDecoder().decode([UsageRow].self, from: fixture("stats.json")).isEmpty)
    #expect(try !JSONDecoder().decode(ToolkitInventory.self, from: fixture("toolkit.json")).agents.isEmpty)
    #expect(try JSONDecoder().decode([DoctorRow].self, from: fixture("doctor.json")).contains { $0.check == "binary git" })
    for id in [running, review, approval, blocked, "261118-currency-g7h8", "261117-categories-k1l2"] {
        #expect(try ticket(id).id == id)
        _ = try events(id)
    }
}

@Test func theConfigSurvivesARoundTripThroughFriday() throws {
    let original = try JSONSerialization.jsonObject(with: fixture("config.json")) as! [String: Any]
    let config = try JSONDecoder().decode(CWConfig.self, from: fixture("config.json"))
    let encoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(config)) as! [String: Any]
    let lost = Set(original.keys).subtracting(encoded.keys).subtracting(["bitbucket"])
    #expect(lost.isEmpty, "keys FRIDAY would drop on save: \(lost)")
}

@Test func aResumedSessionsCostIsCountedOnce() throws {
    let csv = try ticket(running)
    #expect(csv.runs.map(\.costUsd) == [0.84, 0.31, 0.97, 0.58])
    #expect(abs(csv.totalCost - (0.84 + 0.31 + 0.13 + 0.27)) < 0.001)
}

@Test func theRunningWorkerIsFoundFromTheEvents() throws {
    let csv = try ticket(running)
    let active = try #require(activeRun(in: events(running), running: true))
    #expect(active.worker == "W2" && active.task == "S3" && active.tools.count == 3)
    #expect(activeRun(in: try events(running), running: false) == nil)
    let agents = csv.agents(active: active)
    #expect(agents.map(\.id) == ["planner", "W1", "W2", "review:reviewer"])
    #expect(agents.first { $0.id == "W2" }?.state == .working)
    #expect(agents.first { $0.id == "W1" }?.note == "2 of 2 done")
    #expect(agents.first { $0.id == "W1" }?.runs.count == 2)
    let stages = csv.stages(active: active, lastEvent: try events(running).last)
    #expect(stages.first { $0.0 == .plan }?.1 == .done && stages.first { $0.0 == .work }?.1 == .active)
}

@Test func anOpenBlockingFindingKeepsMergeOffAndPutsFixFirst() throws {
    let report = try ticket(review)
    let actions = report.actions(running: false)
    #expect(actions.first?.arguments == ["fix", review])
    #expect(actions.first { $0.arguments.first == "merge" }?.blockedReason != nil)
    #expect(report.agents(active: nil).last?.state == .failed)
    #expect(report.stages(active: nil, lastEvent: nil).first { $0.0 == .review }?.1 == .failed)
}

@Test func eachStatusOffersItsCommandsAndComposerMode() throws {
    let plan = try ticket(approval)
    #expect(plan.actions(running: false).map(\.arguments) == [["approve", approval]])
    #expect(plan.composerMode == .planFeedback)
    let stopped = try ticket(blocked)
    #expect(stopped.actions(running: false).map(\.arguments) == [["resume", blocked]])
    #expect(stopped.stages(active: nil, lastEvent: nil).first { $0.0 == .checks }?.1 == .failed)
    #expect(try ticket("261118-currency-g7h8").composerMode == .answer)
    #expect(try ticket("261117-categories-k1l2").composerMode == .closed)
    #expect(stopped.actions(running: true).isEmpty)
}

@Test func timestampsWithMicrosecondsAreRead() {
    #expect(parseDate("2026-11-18T08:43:17.015408Z") != nil)
    #expect(parseDate("2026-11-18T08:43:17.015408+00:00") != nil)
}
