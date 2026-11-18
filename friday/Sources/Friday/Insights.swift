import Foundation

/// A step of cw's flow, as the pipeline bar shows it.
enum Stage: Int, CaseIterable, Identifiable {
    case plan, work, checks, review, pullRequest, merge
    var id: Int { rawValue }

    var label: String {
        switch self {
        case .plan: "Plan"
        case .work: "Work"
        case .checks: "Checks"
        case .review: "Review"
        case .pullRequest: "PR"
        case .merge: "Merge"
        }
    }
}

enum StageState: Equatable {
    case done, active, waiting, paused, failed, pending, skipped
}

/// The agent run going on now, from the ticket's events.
struct ActiveRun: Equatable {
    var label: String
    var role: String
    var model: String
    var startedAt: Date?
    var tools: [AgentEvent]

    var worker: String { label.components(separatedBy: " · ").first ?? label }
    var task: String? {
        let parts = label.components(separatedBy: " · ")
        return parts.count > 1 ? parts[1] : nil
    }
}

enum AgentState: Equatable {
    case working, waiting, finished, failed, idle
}

/// One agent of a ticket: the planner, a worker (W1, W2, ...) or a reviewer.
struct AgentCard: Identifiable, Equatable {
    var id: String
    var role: AgentRole
    var title: String
    var model: String
    var tasks: [Subtask]
    var runs: [RunUsage]
    var cost: Double
    var state: AgentState
    var note: String
}

enum ComposerMode: Equatable {
    case answer, planFeedback, followUp, closed

    var placeholder: String {
        switch self {
        case .answer: "Answer the questions"
        case .planFeedback: "Tell the planner what to change, or approve the plan"
        case .followUp: "Ask for more work on this ticket"
        case .closed: "This ticket is closed"
        }
    }

    var sendLabel: String {
        switch self {
        case .answer: "Answer"
        case .planFeedback: "Send feedback"
        case .followUp, .closed: "Send"
        }
    }
}

enum ActionStyle {
    case primary, secondary
}

/// A `cw` command the ticket's status allows, as a button.
struct TicketAction: Identifiable, Equatable {
    var title: String
    var systemImage: String
    var arguments: [String]
    var style: ActionStyle
    var blockedReason: String?
    var id: String { arguments.joined(separator: " ") }
}

private let fractionalSeconds = try! NSRegularExpression(pattern: "\\.\\d+")
private let isoFormatter = ISO8601DateFormatter()

/// Parses cw's ISO timestamps, which carry microseconds that ISO8601DateFormatter does not read.
func parseDate(_ text: String) -> Date? {
    let range = NSRange(text.startIndex..., in: text)
    return isoFormatter.date(from: fractionalSeconds.stringByReplacingMatches(in: text, range: range, withTemplate: ""))
}

/// Returns the run going on now: the last start without its end, while cw holds the ticket.
func activeRun(in events: [AgentEvent], running: Bool) -> ActiveRun? {
    guard running, let start = events.lastIndex(where: { $0.kind == "start" }) else { return nil }
    let started = events[start]
    let later = events[(start + 1)...]
    if later.contains(where: { ($0.kind == "done" || $0.kind == "error") && $0.label == started.label }) { return nil }
    return ActiveRun(
        label: started.label ?? started.agent ?? "agent",
        role: started.role ?? "",
        model: started.model ?? "",
        startedAt: parseDate(started.at),
        tools: later.filter { $0.kind == "tool" && $0.label == started.label })
}

extension Ticket {
    /// What each run cost: Claude Code reports a resumed session's running total, so each run is its own share.
    var runCosts: [Double] {
        var spent: [String: Double] = [:]
        return runs.map { run in
            let cost = run.costUsd - (spent[run.sessionId] ?? 0)
            if !run.sessionId.isEmpty { spent[run.sessionId] = run.costUsd }
            return max(cost, 0)
        }
    }

    var totalCost: Double { runCosts.reduce(0, +) }

    var isPlanned: Bool { route == "planned" }

    var workerNames: [String] {
        let named = subtasks.map(\.worker) + workLog.map(\.worker) + Array(workerSessions.keys)
        return Array(Set(named.filter { !$0.isEmpty })).sorted()
    }

    var reviewerNames: [String] {
        let named = reviews.map(\.reviewer) + runs.filter { $0.role == "reviewer" }.map { $0.label.isEmpty ? $0.agent : $0.label }
        return Array(Set(named)).sorted()
    }

    /// The planner, every worker and every reviewer, with their runs, cost and what they are doing now.
    func agents(active: ActiveRun?) -> [AgentCard] {
        let costs = runCosts
        func select(_ matches: (RunUsage) -> Bool) -> ([RunUsage], Double) {
            let indices = self.runs.indices.filter { matches(self.runs[$0]) }
            return (indices.map { self.runs[$0] }, indices.map { costs[$0] }.reduce(0, +))
        }
        func lastModel(_ chosen: [RunUsage], _ fallback: String) -> String { chosen.last?.model ?? fallback }
        var cards: [AgentCard] = []
        if isPlanned || plannerSession != nil || runs.contains(where: { $0.role == "planner" }) {
            let (chosen, cost) = select { $0.role == "planner" }
            let answered = qa.filter { $0.answeredBy == "planner" }.count
            let planning = active?.role == "planner"
            let waiting = status == .needsInput && askingWorker.isEmpty || status == .awaitingApproval
            cards.append(AgentCard(
                id: "planner", role: .planner, title: "Planner", model: lastModel(chosen, active?.model ?? ""),
                tasks: [], runs: chosen, cost: cost,
                state: planning ? .working : waiting ? .waiting : chosen.isEmpty ? .idle : .finished,
                note: subtasks.isEmpty ? (questions.isEmpty ? "no plan yet" : "\(questions.count) question(s) for you")
                    : "\(subtasks.count) tasks" + (answered > 0 ? " · \(answered) answer(s)" : "")))
        }
        for worker in workerNames.isEmpty && !workLog.isEmpty ? ["W1"] : workerNames {
            let (chosen, cost) = select { $0.role == "worker" && ($0.label.isEmpty || $0.label.hasPrefix(worker)) }
            let tasks = subtasks.filter { $0.worker == worker }
            let doneCount = tasks.filter { tasksDone.contains($0.id) }.count
            let working = active?.role == "worker" && active?.worker == worker
            let asking = status == .needsInput && askingWorker == worker
            let failed = status == .blocked && lastWorker == worker
            cards.append(AgentCard(
                id: worker, role: .worker, title: worker, model: lastModel(chosen, working ? active?.model ?? "" : ""),
                tasks: tasks, runs: chosen, cost: cost,
                state: working ? .working : asking ? .waiting : failed ? .failed : chosen.isEmpty && doneCount == 0 ? .idle : .finished,
                note: working ? "on \(active?.task ?? "a fix")" : tasks.isEmpty ? "\(workLog.filter { $0.worker == worker }.count) pass(es)"
                    : "\(doneCount) of \(tasks.count) done"))
        }
        for reviewer in reviewerNames.isEmpty ? ["reviewer"] : reviewerNames {
            let (chosen, cost) = select { $0.role == "reviewer" && ($0.label.isEmpty ? $0.agent : $0.label) == reviewer }
            let open = openFindings.count
            let working = active?.role == "reviewer"
            cards.append(AgentCard(
                id: "review:\(reviewer)", role: .reviewer, title: reviewer == "reviewer" ? "Reviewer" : reviewer,
                model: lastModel(chosen, ""), tasks: [], runs: chosen, cost: cost,
                state: working ? .working : open > 0 ? .failed : chosen.isEmpty ? .idle : .finished,
                note: working ? "reviewing" : reviews.isEmpty ? "not yet" : open > 0 ? "\(open) blocking open" : "approved"))
        }
        return cards
    }

    /// Where the ticket is between plan and merge.
    func stages(active: ActiveRun?, lastEvent: AgentEvent?) -> [(Stage, StageState)] {
        let reached: Int
        switch status {
        case .needsInput, .awaitingApproval:
            reached = Stage.plan.rawValue
        case .inProgress:
            if active?.role == "planner" { reached = Stage.plan.rawValue }
            else if active?.role == "reviewer" { reached = Stage.review.rawValue }
            else if active == nil && lastEvent?.kind == "check" { reached = Stage.checks.rawValue }
            else { reached = Stage.work.rawValue }
        case .ready, .inReview:
            reached = active?.role == "worker" ? Stage.work.rawValue : Stage.review.rawValue
        case .blocked, .paused:
            switch pausedStep {
            case "plan": reached = Stage.plan.rawValue
            case "review": reached = Stage.review.rawValue
            case "pr": reached = Stage.pullRequest.rawValue
            default: reached = failingChecks.isEmpty ? Stage.work.rawValue : Stage.checks.rawValue
            }
        case .prOpen:
            reached = Stage.merge.rawValue
        case .done:
            reached = Stage.allCases.count
        case .abandoned:
            reached = -1
        }
        let here: StageState
        switch status {
        case .blocked: here = .failed
        case .paused: here = .paused
        case .needsInput, .awaitingApproval, .ready, .prOpen: here = active == nil ? .waiting : .active
        default: here = .active
        }
        return Stage.allCases.map { stage in
            if stage == .plan && !isPlanned && plan.isEmpty { return (stage, .skipped) }
            if stage.rawValue < reached { return (stage, stage == .review && !openFindings.isEmpty ? .failed : .done) }
            if stage.rawValue == reached { return (stage, here) }
            return (stage, .pending)
        }
    }

    /// The `cw` commands this ticket's status allows; the first is the one to do next.
    func actions(running: Bool) -> [TicketAction] {
        if running || status.isClosed { return [] }
        switch status {
        case .needsInput:
            return split.isEmpty ? [] : [TicketAction(title: "Start \(split.count) tickets", systemImage: "arrow.triangle.branch", arguments: ["split", id], style: .primary)]
        case .awaitingApproval:
            return [TicketAction(title: "Approve plan", systemImage: "checkmark.seal", arguments: ["approve", id], style: .primary)]
        case .ready:
            return [
                TicketAction(title: prUrl == nil ? "Review and open PR" : "Review and update PR", systemImage: "arrow.up.circle", arguments: ["pr", id], style: .primary),
                TicketAction(title: "Review only", systemImage: "eye", arguments: ["review", id], style: .secondary),
            ]
        case .inProgress, .inReview, .blocked, .paused:
            return [TicketAction(title: "Resume", systemImage: "play", arguments: ["resume", id], style: .primary)]
        case .prOpen:
            guard prUrl != nil else {
                return [TicketAction(title: "Merge into \(targetBranch)", systemImage: "arrow.triangle.merge", arguments: ["done", id], style: .primary)]
            }
            let blocked = openFindings.isEmpty ? nil : "\(openFindings.count) blocking finding(s) are open: fix them first"
            let fix = TicketAction(title: "Fix comments", systemImage: "wrench.and.screwdriver", arguments: ["fix", id], style: blocked == nil ? .secondary : .primary)
            let merge = TicketAction(title: "Merge", systemImage: "arrow.triangle.merge", arguments: ["merge", id], style: blocked == nil ? .primary : .secondary, blockedReason: blocked)
            let ci = TicketAction(title: "CI status", systemImage: "checklist", arguments: ["ci", id], style: .secondary)
            return blocked == nil ? [merge, fix, ci] : [fix, merge, ci]
        case .done, .abandoned:
            return []
        }
    }

    var composerMode: ComposerMode {
        switch status {
        case .done, .abandoned: .closed
        case .needsInput: split.isEmpty ? .answer : .planFeedback
        case .awaitingApproval: .planFeedback
        default: .followUp
        }
    }

    /// One sentence on what happens next, for the next-step bar.
    func nextStep(active: ActiveRun?) -> String {
        if let active {
            return "\(active.worker) is working" + (active.task.map { " on \($0)" } ?? "") + "."
        }
        switch status {
        case .needsInput: return split.isEmpty ? "Answer the questions below to continue." : "This is several changes: start one ticket for each."
        case .awaitingApproval: return "Read the plan, then approve it or tell the planner what to change."
        case .inProgress, .inReview: return "This step stopped before it finished. Resume it."
        case .ready: return "The work is done. Review it and open the pull request."
        case .blocked: return error ?? "Something stopped this ticket. Fix the cause, then resume."
        case .paused: return "Paused at the usage limit. Resume when it resets."
        case .prOpen:
            if prUrl == nil { return "Committed on \(branch). Merge it into \(targetBranch)." }
            return openFindings.isEmpty ? "Merge once CI has passed, or let the worker address new comments." : "Fix the open blocking findings before merging."
        case .done: return "Merged and closed."
        case .abandoned: return "Abandoned."
        }
    }
}

extension TicketSummary {
    var needsYou: Bool { !running && [.needsInput, .awaitingApproval, .blocked, .paused, .ready].contains(status) }
}

extension Double {
    var dollars: String { String(format: "$%.2f", self) }
}

extension Int {
    var compact: String {
        switch self {
        case 1_000_000...: String(format: "%.1fM", Double(self) / 1_000_000)
        case 1_000...: String(format: "%.1fk", Double(self) / 1_000)
        default: "\(self)"
        }
    }
}
