import SwiftUI

/// One ticket: where it is, which agents work on it, what they did, and what you can do next.
struct TicketDetailView: View {
    @Environment(Store.self) private var store
    @Environment(\.isSnapshot) private var isSnapshot
    let ticketId: String
    @State private var feed: TicketFeed

    init(ticketId: String) {
        self.ticketId = ticketId
        _feed = State(initialValue: TicketFeed(ticketId: ticketId))
    }

    var body: some View {
        @Bindable var store = store
        Group {
            if let ticket = feed.ticket {
                let running = store.isRunning(ticketId)
                let active = activeRun(in: feed.events, running: running)
                let agents = ticket.agents(active: active)
                VStack(spacing: 0) {
                    TicketHeader(ticket: ticket, running: running)
                    PipelineBar(stages: ticket.stages(active: active, lastEvent: feed.events.last), ticket: ticket)
                    AgentStrip(agents: agents, selected: $store.showAgent)
                    Picker("Section", selection: $store.ticketTab) {
                        ForEach(TicketTab.allCases) { tab in
                            Text(tabTitle(tab, ticket: ticket)).tag(tab)
                        }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .padding(.horizontal, 20)
                    .padding(.vertical, 10)
                    Divider()
                    ScrollView {
                        tabContent(ticket, active: active)
                            .padding(20)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Divider()
                    NextStepBar(ticket: ticket, active: active, running: running)
                    Composer(ticket: ticket, running: running)
                }
                .background(Color(nsColor: .windowBackgroundColor))
                .inspector(isPresented: Binding(get: { store.showAgent != nil && !isSnapshot }, set: { if !$0 { store.showAgent = nil } })) {
                    if let card = agents.first(where: { $0.id == store.showAgent }) {
                        AgentInspector(card: card, ticket: ticket, active: active)
                            .inspectorColumnWidth(min: 280, ideal: 320, max: 420)
                    }
                }
                .overlay(alignment: .trailing) {
                    if isSnapshot, let card = agents.first(where: { $0.id == store.showAgent }) {
                        HStack(spacing: 0) {
                            Divider()
                            AgentInspector(card: card, ticket: ticket, active: active)
                                .frame(width: 320)
                                .background(Color(nsColor: .windowBackgroundColor))
                        }
                    }
                }
            } else if feed.loaded {
                ContentUnavailableView("Ticket not found", systemImage: "questionmark.folder", description: Text(ticketId))
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task { await feed.follow(using: store) }
    }

    private func tabTitle(_ tab: TicketTab, ticket: Ticket) -> String {
        switch tab {
        case .plan where !ticket.subtasks.isEmpty: "Plan \(ticket.tasksDone.count)/\(ticket.subtasks.count)"
        case .review where !ticket.openFindings.isEmpty: "Review · \(ticket.openFindings.count) open"
        default: tab.rawValue
        }
    }

    @ViewBuilder
    private func tabContent(_ ticket: Ticket, active: ActiveRun?) -> some View {
        switch store.ticketTab {
        case .overview: OverviewTab(ticket: ticket, active: active)
        case .plan: PlanTab(ticket: ticket)
        case .work: WorkTab(ticket: ticket)
        case .review: ReviewTab(ticket: ticket)
        case .activity: ActivityTab(ticket: ticket, events: feed.events, job: store.job(for: ticket.id))
        case .cost: CostTab(ticket: ticket)
        }
    }
}

private struct TicketHeader: View {
    @Environment(Store.self) private var store
    let ticket: Ticket
    let running: Bool
    @State private var confirmAbandon = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text(ticket.title).font(.title2.weight(.semibold)).lineLimit(2).textSelection(.enabled)
                HStack(spacing: 10) {
                    StatusBadge(status: ticket.status, running: running)
                    Label(ticket.branch, systemImage: "arrow.triangle.branch").help("from \(ticket.baseBranch)")
                    Label(ticket.isPlanned ? "Planned" : "Direct", systemImage: ticket.isPlanned ? "map" : "bolt")
                    if let url = ticket.prUrl.flatMap(URL.init(string:)) {
                        Link(destination: url) { Label("PR #\(ticket.prNumber ?? 0)", systemImage: "arrow.triangle.pull") }
                    }
                    Text(ticket.totalCost.dollars).monospacedDigit().help("What this ticket's agent runs cost so far")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            if let job = store.job(for: ticket.id), job.running {
                Button(role: .destructive) { store.stop(job) } label: { Label("Stop", systemImage: "stop.fill") }
                    .help("Stops the agent; the ticket stays resumable")
            }
            Menu {
                Button("Copy ticket id") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(ticket.id, forType: .string)
                }
                Divider()
                Button("Park: save as WIP and switch back") { store.run("Park", project: ticket.project, ticketId: ticket.id, ["park", ticket.id]) }
                    .disabled(running || ticket.status.isClosed)
                Button("Done: close it and switch back") { store.run("Done", project: ticket.project, ticketId: ticket.id, ["done", ticket.id]) }
                    .disabled(running || ticket.status.isClosed)
                Divider()
                Button("Abandon…", role: .destructive) { confirmAbandon = true }
                    .disabled(running || ticket.status.isClosed)
            } label: {
                Label("More", systemImage: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
        }
        .padding(.horizontal, 20)
        .padding(.top, 16)
        .padding(.bottom, 8)
        .confirmationDialog("Abandon “\(ticket.title)”?", isPresented: $confirmAbandon) {
            Button("Abandon and delete the branch", role: .destructive) {
                store.run("Abandon", project: ticket.project, ticketId: ticket.id, ["done", ticket.id, "--abandon"])
            }
        } message: {
            Text("Its uncommitted changes are discarded and \(ticket.branch) is deleted. This can't be undone.")
        }
    }
}

private struct PipelineBar: View {
    let stages: [(Stage, StageState)]
    let ticket: Ticket

    var body: some View {
        HStack(spacing: 6) {
            ForEach(stages, id: \.0) { stage, state in
                HStack(spacing: 5) {
                    icon(state)
                    Text(title(stage)).lineLimit(1)
                }
                .font(.caption.weight(state == .active || state == .waiting ? .semibold : .regular))
                .padding(.vertical, 6)
                .frame(maxWidth: .infinity)
                .background(color(state).opacity(state == .pending || state == .skipped ? 0.05 : 0.14), in: RoundedRectangle(cornerRadius: 7))
                .foregroundStyle(state == .pending || state == .skipped ? Color.secondary : color(state))
                .strikethrough(state == .skipped)
                .help(help(stage, state))
            }
        }
        .padding(.horizontal, 20)
        .padding(.bottom, 10)
    }

    private func title(_ stage: Stage) -> String {
        if stage == .work, !ticket.subtasks.isEmpty { return "Work \(ticket.tasksDone.count)/\(ticket.subtasks.count)" }
        return stage.label
    }

    @ViewBuilder
    private func icon(_ state: StageState) -> some View {
        switch state {
        case .done: Image(systemName: "checkmark")
        case .active: ProgressView().controlSize(.mini)
        case .waiting: Image(systemName: "hand.raised")
        case .paused: Image(systemName: "pause")
        case .failed: Image(systemName: "exclamationmark.triangle")
        case .pending, .skipped: Image(systemName: "circle").imageScale(.small)
        }
    }

    private func color(_ state: StageState) -> Color {
        switch state {
        case .done: .green
        case .active: .blue
        case .waiting: .orange
        case .paused: .purple
        case .failed: .red
        case .pending, .skipped: .secondary
        }
    }

    private func help(_ stage: Stage, _ state: StageState) -> String {
        switch state {
        case .skipped: "Skipped: this ticket went straight to a worker"
        case .waiting: "\(stage.label): waiting for you"
        case .failed: "\(stage.label): stopped or left findings open"
        default: stage.label
        }
    }
}
