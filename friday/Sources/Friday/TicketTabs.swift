import Charts
import SwiftUI

/// A titled block inside a tab.
struct Block<Content: View>: View {
    var title: String
    var systemImage: String
    var tint: Color = .secondary
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage).font(.headline).foregroundStyle(tint == .secondary ? .primary : tint)
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint == .secondary ? Theme.card : tint.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
    }
}

struct OverviewTab: View {
    let ticket: Ticket
    let active: ActiveRun?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let error = ticket.error, ticket.status == .blocked || ticket.status == .paused {
                Block(title: ticket.status == .paused ? "Paused" : "Stopped", systemImage: "exclamationmark.octagon", tint: .red) {
                    Text(error).textSelection(.enabled)
                    if !ticket.failingChecks.isEmpty {
                        Text("Failing: " + ticket.failingChecks.joined(separator: ", ")).font(.callout.monospaced())
                    }
                }
            }
            if !ticket.questions.isEmpty {
                Block(title: ticket.split.isEmpty ? "Questions for you" : "Several changes", systemImage: "questionmark.bubble", tint: .orange) {
                    ForEach(Array(ticket.questions.enumerated()), id: \.offset) { index, question in
                        Text("\(index + 1). \(question)").textSelection(.enabled)
                    }
                    ForEach(ticket.split, id: \.self) { Label($0, systemImage: "arrow.turn.down.right") }
                    Text(ticket.split.isEmpty ? "Answer in the box below." : "Start them as separate tickets, or reply to keep them together.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if !ticket.warnings.isEmpty {
                Block(title: "Warnings", systemImage: "exclamationmark.triangle", tint: .yellow) {
                    ForEach(ticket.warnings, id: \.self) { Text($0).font(.callout).textSelection(.enabled) }
                }
            }
            Block(title: "Request", systemImage: "text.bubble") {
                ForEach(Array(ticket.prompts.enumerated()), id: \.offset) { _, prompt in
                    HStack(alignment: .top) {
                        Image(systemName: prompt.isAnswer ? "arrowshape.turn.up.left" : "person").foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            MarkdownText(text: prompt.text)
                            RelativeTime(timestamp: prompt.at).font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                }
            }
            if !ticket.qa.isEmpty {
                Block(title: "Questions the workers asked", systemImage: "bubble.left.and.bubble.right") {
                    ForEach(Array(ticket.qa.enumerated()), id: \.offset) { _, entry in
                        VStack(alignment: .leading, spacing: 3) {
                            Text("\(entry.worker)\(entry.task.map { " on \($0)" } ?? ""): \(entry.question)").font(.callout.weight(.medium))
                            Text(entry.answer.isEmpty ? "Waiting for your answer" : "\(entry.answeredBy.capitalized): \(entry.answer)")
                                .font(.callout)
                                .foregroundStyle(entry.answer.isEmpty ? .orange : .secondary)
                        }
                    }
                }
            }
            Block(title: "Branch", systemImage: "arrow.triangle.branch") {
                Grid(alignment: .leading, verticalSpacing: 4) {
                    row("Branch", ticket.branch)
                    row("From", ticket.baseBranch)
                    row("Goes back into", ticket.targetBranch)
                    row("You were on", ticket.previousBranch)
                    if !ticket.commitMessage.isEmpty { row("Commit", ticket.commitMessage) }
                    row("Toolkit", ticket.toolkitVersion)
                }
                .font(.callout)
            }
        }
    }

    private func row(_ title: String, _ value: String) -> some View {
        GridRow {
            Text(title).foregroundStyle(.secondary)
            Text(value).textSelection(.enabled)
        }
    }
}

struct PlanTab: View {
    let ticket: Ticket

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if ticket.plan.isEmpty && ticket.subtasks.isEmpty {
                ContentUnavailableView("No plan", systemImage: "map", description: Text(ticket.isPlanned ? "The planner hasn't written one yet." : "This ticket went straight to a worker."))
            } else {
                Block(title: "Plan", systemImage: "map", tint: .purple) { MarkdownText(text: ticket.plan) }
                if !ticket.subtasks.isEmpty {
                    Block(title: "Tasks", systemImage: "checklist") {
                        ForEach(ticket.subtasks) { task in TaskRow(task: task, done: ticket.tasksDone.contains(task.id)) }
                    }
                }
                if !ticket.optionsConsidered.isEmpty {
                    Block(title: "Considered and set aside", systemImage: "arrow.uturn.left") { MarkdownText(text: ticket.optionsConsidered) }
                }
                if !ticket.contextPack.isEmpty {
                    Block(title: "Code to read first", systemImage: "scope") {
                        ForEach(ticket.contextPack, id: \.self) { anchor in
                            VStack(alignment: .leading, spacing: 1) {
                                Text(anchor.path + (anchor.lines.isEmpty ? "" : ":\(anchor.lines)")).font(.callout.monospaced())
                                if !anchor.reason.isEmpty { Text(anchor.reason).font(.caption).foregroundStyle(.secondary) }
                            }
                        }
                    }
                }
            }
        }
    }
}

private struct TaskRow: View {
    let task: Subtask
    let done: Bool
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 6) {
                MarkdownText(text: task.instructions).font(.callout)
                if !task.files.isEmpty {
                    Text(task.files.joined(separator: "\n")).font(.caption.monospaced()).foregroundStyle(.secondary)
                }
            }
            .padding(.top, 4)
        } label: {
            HStack {
                Image(systemName: done ? "checkmark.circle.fill" : "circle").foregroundStyle(done ? .green : .secondary)
                Text("\(task.id) · \(task.title)")
                Spacer()
                Pill(text: task.worker, color: Theme.color(for: .worker))
            }
        }
    }
}

struct WorkTab: View {
    let ticket: Ticket

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if ticket.workLog.isEmpty {
                ContentUnavailableView("No work yet", systemImage: "hammer", description: Text("Each worker pass shows up here: what it did, its files and what it handed on."))
            }
            ForEach(Array(ticket.workLog.enumerated().reversed()), id: \.offset) { _, entry in
                Block(title: title(entry), systemImage: entry.status == "done" ? "checkmark.circle" : "exclamationmark.circle", tint: entry.status == "done" ? .secondary : .orange) {
                    MarkdownText(text: entry.summary)
                    if !entry.files.isEmpty {
                        Text(entry.files.joined(separator: "\n")).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                    }
                    if !entry.handoff.isEmpty {
                        Label { MarkdownText(text: entry.handoff) } icon: { Image(systemName: "arrow.right.circle") }
                            .font(.callout)
                    }
                    ForEach(entry.projectNotes, id: \.self) { Label($0, systemImage: "note.text").font(.caption) }
                    HStack {
                        Pill(text: entry.worker, color: Theme.color(for: .worker))
                        RelativeTime(timestamp: entry.at).font(.caption).foregroundStyle(.tertiary)
                    }
                }
            }
        }
    }

    private func title(_ entry: WorkEntry) -> String {
        guard let id = entry.subtask, let task = ticket.subtasks.first(where: { $0.id == id }) else { return entry.subtask ?? "Follow-up" }
        return "\(task.id) · \(task.title)"
    }
}

struct ReviewTab: View {
    let ticket: Ticket

    private var openIds: Set<String> { Set(ticket.openFindings.map(\.id)) }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if ticket.reviews.isEmpty {
                ContentUnavailableView("Not reviewed yet", systemImage: "eye", description: Text("Review only or Review and open PR runs the reviewer on the change."))
            } else {
                HStack {
                    Pill(text: "\(ticket.openFindings.count) blocking open", color: ticket.openFindings.isEmpty ? .green : .red)
                    Pill(text: "\(ticket.reviews.count) review(s)")
                    if let url = ticket.prUrl.flatMap(URL.init(string:)) { Link("Open the pull request", destination: url).font(.caption) }
                }
                if !ticket.openFindings.isEmpty {
                    Block(title: "Open blocking findings", systemImage: "exclamationmark.triangle", tint: .red) {
                        ForEach(ticket.openFindings, id: \.self) { FindingRow(finding: $0, state: "open") }
                        Text("cw merge stays off until a review says these are fixed. Fix comments sends them to the worker.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                ForEach(Array(ticket.reviews.enumerated().reversed()), id: \.offset) { _, review in
                    Block(title: "Round \(review.round) · \(review.reviewer) · \(review.verdict)", systemImage: review.verdict == "approve" ? "checkmark.seal" : "eye") {
                        MarkdownText(text: review.summary)
                        ForEach(review.comments, id: \.self) { comment in
                            FindingRow(finding: comment, state: !comment.isBlocking ? "minor" : openIds.contains(comment.id) ? "open" : "fixed")
                        }
                    }
                }
            }
        }
    }
}

private struct FindingRow: View {
    let finding: ReviewComment
    let state: String

    private var color: Color {
        switch state {
        case "open": .red
        case "fixed": .green
        default: .secondary
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Pill(text: [finding.id, state].filter { !$0.isEmpty }.joined(separator: " "), color: color)
                Text("\(finding.path):\(finding.line)").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
            }
            Text(finding.body).font(.callout).strikethrough(state == "fixed").textSelection(.enabled)
        }
        .padding(.vertical, 2)
    }
}

struct ActivityTab: View {
    @Environment(Store.self) private var store
    let ticket: Ticket
    let events: [AgentEvent]
    let job: Job?
    @State private var showOutput = false

    private var retentionDays: Int {
        store.config?.storage?.eventRetentionDays ?? StorageSettings.defaultEventRetentionDays
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if events.isEmpty && ticket.archived {
                ContentUnavailableView("Activity log removed", systemImage: "clock.arrow.circlepath", description: Text("Closed tickets keep their activity log for \(retentionDays) days; cw gc removes it after that. The ticket itself stays."))
            } else if events.isEmpty {
                ContentUnavailableView("No activity yet", systemImage: "waveform.path.ecg", description: Text("Every agent start, tool call, end and check shows up here, wherever the run was started."))
            } else {
                Block(title: "Agent activity", systemImage: "waveform.path.ecg") {
                    ForEach(Array(events.enumerated().reversed()), id: \.offset) { _, event in EventRow(event: event) }
                }
            }
            if let job {
                DisclosureGroup("Output of “\(job.title)”", isExpanded: $showOutput) {
                    LogView(text: job.log)
                }
            }
        }
    }
}

struct EventRow: View {
    let event: AgentEvent

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: symbol).foregroundStyle(color).frame(width: 16)
            VStack(alignment: .leading, spacing: 1) {
                Text(text).font(event.kind == "tool" ? .caption.monospaced() : .callout).lineLimit(2).textSelection(.enabled)
                if let label = event.label, event.kind != "check" { Text(label).font(.caption2).foregroundStyle(.secondary) }
            }
            Spacer()
            RelativeTime(timestamp: event.at).font(.caption2).foregroundStyle(.tertiary)
        }
    }

    private var text: String {
        switch event.kind {
        case "start": "Started on \(event.model ?? "")"
        case "done": "Finished in \(event.turns ?? 0) turns"
        case "error": "Failed: \(event.message ?? "")"
        case "check": "$ \(event.command ?? "")" + (event.passed == true ? "  passed" : "  failed")
        default: "\(event.tool ?? "") \(event.detail ?? "")"
        }
    }

    private var symbol: String {
        switch event.kind {
        case "start": "play.circle"
        case "done": "checkmark.circle"
        case "error": "xmark.octagon"
        case "check": event.passed == true ? "checkmark.shield" : "xmark.shield"
        default: "wrench.adjustable"
        }
    }

    private var color: Color {
        switch event.kind {
        case "done": .green
        case "error": .red
        case "check": event.passed == true ? .green : .red
        case "start": .blue
        default: .secondary
        }
    }
}

struct CostTab: View {
    let ticket: Ticket

    private struct Row: Identifiable {
        var id: Int
        var run: RunUsage
        var cost: Double
        var who: String { run.label.isEmpty ? run.agent : run.label.components(separatedBy: " · ").first ?? run.label }
    }

    private var rows: [Row] {
        zip(ticket.runs, ticket.runCosts).enumerated().map { Row(id: $0, run: $1.0, cost: $1.1) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if rows.isEmpty {
                ContentUnavailableView("No runs yet", systemImage: "dollarsign.circle", description: Text("Each agent run's tokens and cost show up here."))
            } else {
                HStack {
                    MetricTile(title: "Total", value: ticket.totalCost.dollars, systemImage: "dollarsign.circle")
                    MetricTile(title: "Runs", value: "\(rows.count)", systemImage: "play.circle")
                    MetricTile(title: "Turns", value: "\(ticket.runs.map(\.numTurns).reduce(0, +))", systemImage: "arrow.triangle.2.circlepath")
                }
                Chart(rows) { row in
                    BarMark(x: .value("Cost", row.cost), y: .value("Agent", row.who))
                        .foregroundStyle(by: .value("Role", row.run.role))
                }
                .chartForegroundStyleScale(["planner": Color.purple, "worker": Color.blue, "reviewer": Color.orange])
                .frame(height: CGFloat(max(Set(rows.map(\.who)).count, 1)) * 34 + 30)
                Table(rows) {
                    TableColumn("Run") { Text($0.run.label.isEmpty ? $0.run.agent : $0.run.label) }
                    TableColumn("Model") { Text($0.run.model) }
                    TableColumn("Turns") { Text("\($0.run.numTurns)").monospacedDigit() }
                    TableColumn("In / out") { Text("\($0.run.inputTokens.compact) / \($0.run.outputTokens.compact)").monospacedDigit() }
                    TableColumn("Cached") { Text($0.run.cacheReadTokens.compact).monospacedDigit() }
                    TableColumn("Cost") { Text($0.cost.dollars).monospacedDigit() }
                }
                .frame(minHeight: CGFloat(rows.count) * 26 + 34)
            }
        }
    }
}

/// The raw output of a command FRIDAY ran.
struct LogView: View {
    let text: String

    var body: some View {
        ScrollView {
            Text(text.isEmpty ? "No output yet" : text)
                .font(.caption.monospaced())
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
        }
        .frame(minHeight: 120, maxHeight: 320)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 8))
    }
}
