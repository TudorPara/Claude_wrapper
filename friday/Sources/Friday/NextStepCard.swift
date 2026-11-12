import AppKit
import SwiftUI

/// The one place that says, in plain words: where the ticket is, where its code is, what is wrong, and what to do next.
struct NextStepCard: View {
    @Environment(Store.self) private var store
    var ticket: Ticket
    var compact = false
    @State private var changed = 0

    private var summary: TicketSummary? { store.tickets.first { $0.id == ticket.id } }
    private var running: Bool { summary?.running == true || store.job(for: ticket.id)?.running == true }
    private var closed: Bool { ticket.status == .done || ticket.status == .abandoned }
    private var checkout: String { ((store.project(ticket.project)?.path ?? "") as NSString).expandingTildeInPath }
    private var onBranch: Bool { store.info(ticket.project)?.branch == ticket.branch }
    private var reviewWarning: Bool { (ticket.warnings ?? []).contains { $0.hasPrefix("Review:") } }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                StatusChip(status: ticket.status, running: running)
                Text(running ? "Working… watch the live output." : headline).font(compact ? .callout.weight(.semibold) : .headline)
                Spacer()
            }
            codeLocation
            ForEach(ticket.warnings ?? [], id: \.self) { warning in
                Label(warning, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.yellow)
                    .textSelection(.enabled)
            }
            if let error = ticket.error, ticket.status == .blocked || ticket.status == .paused {
                Label(error, systemImage: "xmark.octagon.fill").font(.caption).foregroundStyle(.red).textSelection(.enabled)
            }
            if let split = ticket.split, !split.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(split.enumerated()), id: \.offset) { index, item in
                        Text("\(index + 1). \(item)").font(.callout)
                    }
                }
            }
            if !closed { buttons.disabled(running) }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.color(for: ticket.status).opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.color(for: ticket.status).opacity(0.5)))
        .task(id: summary?.updatedAt) {
            changed = onBranch ? changedFiles(in: checkout).count : 0
        }
    }

    private var headline: String {
        switch ticket.status {
        case .needsInput: (ticket.split ?? []).isEmpty ? "Answer the planner's questions in the chat." : "This is several changes: start one ticket for each."
        case .awaitingApproval: "Read the plan, then approve it or reply to change it."
        case .inProgress: "Working…"
        case .ready: reviewWarning ? "The reviewer still has findings: follow up, or open the PR anyway." : "Done and checked. Follow up in the chat, or open the PR."
        case .inReview: "Reviewing…"
        case .blocked: "Stopped on an error: fix it, then Resume, or follow up in the chat."
        case .paused: "Paused on the usage limit: Resume later."
        case .prOpen: "PR open: merge it on the code host, then mark done."
        case .done: "Done."
        case .abandoned: "Abandoned."
        }
    }

    @ViewBuilder
    private var codeLocation: some View {
        if let pushed = ticket.pushedAt {
            HStack(spacing: 6) {
                Image(systemName: "icloud.and.arrow.up").foregroundStyle(.green)
                Text("Pushed \(pushed.shortTimestamp) to \(ticket.branch)").font(.caption)
                if let url = ticket.prUrl, let link = URL(string: url) {
                    Link("Open PR", destination: link).font(.caption.bold())
                }
            }
        } else if !closed {
            HStack(spacing: 6) {
                Image(systemName: "laptopcomputer").foregroundStyle(.secondary)
                Text(onBranch
                    ? (changed == 0 ? "Local only · on \(ticket.branch) in your checkout · no changes yet" : "Local only · \(changed) changed file\(changed == 1 ? "" : "s") on \(ticket.branch), not committed or pushed")
                    : "Parked on \(ticket.branch) · your checkout is on another branch")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var buttons: some View {
        HStack {
            switch ticket.status {
            case .needsInput where !(ticket.split ?? []).isEmpty:
                primary("Start \(ticket.split!.count) tickets", ["split", ticket.id])
            case .awaitingApproval:
                primary("Approve plan", ["approve", ticket.id])
            case .ready:
                primary(ticket.prUrl == nil ? "Review & open PR" : "Review & update PR", ["pr", ticket.id])
                if reviewWarning { secondary("Open PR anyway", ["pr", ticket.id, "--skip-review"]) }
            case .blocked, .paused:
                primary("Resume", ["resume", ticket.id])
            case .prOpen:
                primary("Mark done", ["done", ticket.id])
                secondary("CI status", ["ci", ticket.id])
            default:
                EmptyView()
            }
            Spacer()
            Menu("More") {
                if onBranch { Button("Park: save as WIP and switch back to \(ticket.previousBranch ?? "your branch")") { run("Park", ["park", ticket.id]) } }
                Button("Done: switch back, keep the branch") { run("Done", ["done", ticket.id]) }
                Button("Abandon: discard its changes and delete the branch", role: .destructive) { run("Abandon", ["done", ticket.id, "--abandon"]) }
            }
            .fixedSize()
        }
        .controlSize(compact ? .small : .regular)
    }

    private func primary(_ title: String, _ arguments: [String]) -> some View {
        Button(title) { run(title, arguments) }.buttonStyle(.borderedProminent)
    }

    private func secondary(_ title: String, _ arguments: [String]) -> some View {
        Button(title) { run(title, arguments) }
    }

    private func run(_ title: String, _ arguments: [String]) {
        store.run("\(title) · \(ticket.title.prefix(40))", project: ticket.project, ticketId: ticket.id, arguments)
    }
}
