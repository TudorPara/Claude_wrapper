import AppKit
import SwiftUI

struct TicketDetailView: View {
    @Environment(Store.self) private var store
    var ticketId: String
    @State private var ticket: Ticket?

    private var summary: TicketSummary? { store.tickets.first { $0.id == ticketId } }
    private var job: Job? { store.job(for: ticketId) }
    private var running: Bool { summary?.running == true || job?.running == true }

    var body: some View {
        ScrollView {
            if let ticket {
                VStack(alignment: .leading, spacing: 16) {
                    header(ticket)
                    actions(ticket)
                    if !ticket.questions.isEmpty { questions(ticket) }
                    if let job { Card(title: "Live output", systemImage: "terminal") { LogView(text: job.log) } }
                    prompts(ticket)
                    if !ticket.plan.isEmpty { plan(ticket) }
                    if !ticket.workLog.isEmpty { workLog(ticket) }
                    if !ticket.reviews.isEmpty { reviews(ticket) }
                    if !ticket.runs.isEmpty { usage(ticket) }
                }
                .padding(20)
            } else {
                ProgressView().padding(40)
            }
        }
        .task(id: summary?.updatedAt) { ticket = await store.ticket(ticketId) }
    }

    private func header(_ ticket: Ticket) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                StatusChip(status: ticket.status, running: running)
                Text(ticket.route == "planned" ? "planned" : "direct").font(.caption).foregroundStyle(.secondary)
                Spacer()
                if let url = ticket.prUrl, let link = URL(string: url) {
                    Link(destination: link) { Label("Open PR", systemImage: "arrow.up.right.square") }
                }

            }
            Text(ticket.title).font(.title2.bold()).textSelection(.enabled)
            Text("\(ticket.id) · \(ticket.branch) · from \(ticket.baseBranch) · \(ticket.owner)")
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
            PipelineView(status: ticket.status, route: ticket.route)
            if let error = ticket.error {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .font(.callout)
                    .textSelection(.enabled)
            }
        }
    }

    private func actions(_ ticket: Ticket) -> some View {
        NextStepCard(ticket: ticket)
    }

    private func questions(_ ticket: Ticket) -> some View {
        Card(title: "Questions from the planner", systemImage: "questionmark.bubble") {
            ForEach(ticket.questions, id: \.self) { Text("• \($0)").textSelection(.enabled) }
        }
    }

    private func prompts(_ ticket: Ticket) -> some View {
        Card(title: "Prompts", systemImage: "text.bubble") {
            ForEach(ticket.prompts, id: \.self) { entry in
                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.at.shortTimestamp).font(.caption2).foregroundStyle(.secondary)
                    Text(entry.text).textSelection(.enabled)
                }
            }
        }
    }

    private func plan(_ ticket: Ticket) -> some View {
        Card(title: "Plan", systemImage: "list.bullet.rectangle") {
            Text(markdown(ticket.plan)).textSelection(.enabled)
            if !ticket.optionsConsidered.isEmpty {
                DisclosureGroup("Options considered") { Text(markdown(ticket.optionsConsidered)).textSelection(.enabled) }
            }
            ForEach(ticket.subtasks, id: \.self) { subtask in
                VStack(alignment: .leading, spacing: 4) {
                    let done = (ticket.tasksDone ?? []).contains(subtask.id)
                    Label("\(subtask.id) · \(subtask.title)", systemImage: done ? "checkmark.circle.fill" : "circle")
                        .fontWeight(.semibold)
                        .foregroundStyle(done ? .green : .primary)
                    Text(subtask.files.joined(separator: "  ")).font(.caption.monospaced()).foregroundStyle(.secondary)
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    private func workLog(_ ticket: Ticket) -> some View {
        Card(title: "Work log", systemImage: "hammer") {
            ForEach(ticket.workLog, id: \.self) { entry in
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(entry.at.shortTimestamp) · \(entry.subtask ?? "worker") · \(entry.status)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(entry.summary).textSelection(.enabled)
                }
            }
        }
    }

    private func reviews(_ ticket: Ticket) -> some View {
        Card(title: "Review", systemImage: "checkmark.seal") {
            ForEach(ticket.reviews, id: \.self) { review in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Round \(review.round) · \(review.reviewer)").fontWeight(.semibold)
                        Text(review.verdict).foregroundStyle(review.verdict == "approve" ? .green : .orange)
                    }
                    Text(review.summary).textSelection(.enabled)
                    ForEach(review.comments, id: \.self) { comment in
                        HStack(alignment: .top) {
                            Text(comment.severity)
                                .font(.caption2.bold())
                                .foregroundStyle(comment.severity == "blocking" ? .red : .secondary)
                                .frame(width: 60, alignment: .leading)
                            Text("\(comment.path):\(comment.line)").font(.caption.monospaced())
                            Text(comment.body).font(.callout)
                        }
                        .textSelection(.enabled)
                    }
                }
            }
        }
    }

    private func usage(_ ticket: Ticket) -> some View {
        Card(title: "Agent runs", systemImage: "gauge.with.dots.needle.33percent") {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
                GridRow {
                    ForEach(["Role", "Agent", "Model", "Input", "Output", "Cached", "Turns", "Time"], id: \.self) {
                        Text($0).font(.caption.bold()).foregroundStyle(.secondary)
                    }
                }
                ForEach(ticket.runs, id: \.self) { run in
                    GridRow {
                        Text(run.role)
                        Text(run.agent)
                        Text(run.model).font(.caption.monospaced())
                        Text((run.inputTokens + run.cacheCreationTokens).compact)
                        Text(run.outputTokens.compact)
                        Text(run.cacheReadTokens.compact)
                        Text("\(run.numTurns)")
                        Text("\(run.durationMs / 1000)s")
                    }
                    .font(.callout)
                }
            }
        }
    }

    private func run(_ ticket: Ticket, _ action: String, _ arguments: [String]) {
        store.run("\(action) · \(ticket.title.prefix(50))", project: ticket.project, ticketId: ticket.id, arguments)
    }

    private func markdown(_ text: String) -> AttributedString {
        (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
    }
}
