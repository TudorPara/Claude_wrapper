import SwiftUI

/// The ticket's agents side by side; the one working now is highlighted. Clicking one opens its details.
struct AgentStrip: View {
    let agents: [AgentCard]
    @Binding var selected: String?

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(agents) { card in
                    AgentCardView(card: card, isSelected: selected == card.id)
                        .onTapGesture { selected = selected == card.id ? nil : card.id }
                        .accessibilityAddTraits(.isButton)
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 8)
        }
    }
}

private struct AgentCardView: View {
    let card: AgentCard
    let isSelected: Bool

    private var color: Color { Theme.color(for: card.role) }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: Theme.symbol(for: card.role))
                    .foregroundStyle(color)
                    .symbolEffect(.pulse, isActive: card.state == .working)
                Text(card.title).font(.callout.weight(.semibold))
                Spacer(minLength: 4)
                stateIcon
            }
            Text(card.model.isEmpty ? card.role.label : "\(card.model) · " + (card.runs.isEmpty ? "first run" : "\(card.runs.count) run(s)"))
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(card.note)
                .font(.caption)
                .foregroundStyle(card.state == .working ? color : card.state == .failed ? .red : .secondary)
                .lineLimit(1)
        }
        .padding(10)
        .frame(width: 168, alignment: .leading)
        .background(card.state == .working ? color.opacity(0.10) : Theme.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(isSelected || card.state == .working ? color : Theme.border, lineWidth: isSelected || card.state == .working ? 1.5 : 1)
        )
        .contentShape(RoundedRectangle(cornerRadius: 10))
        .help("Show \(card.title)'s runs, tasks and handoffs")
    }

    @ViewBuilder
    private var stateIcon: some View {
        switch card.state {
        case .working: ProgressView().controlSize(.mini)
        case .waiting: Image(systemName: "hand.raised.fill").foregroundStyle(.orange)
        case .finished: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed: Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
        case .idle: Image(systemName: "moon.zzz").foregroundStyle(.tertiary)
        }
    }
}

/// One agent in detail: its tasks, what it handed on, the questions it asked or answered, and every run.
struct AgentInspector: View {
    let card: AgentCard
    let ticket: Ticket
    let active: ActiveRun?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Image(systemName: Theme.symbol(for: card.role)).font(.title2).foregroundStyle(Theme.color(for: card.role))
                    VStack(alignment: .leading) {
                        Text(card.title).font(.title3.weight(.semibold))
                        Text("\(card.role.label) · \(card.model.isEmpty ? "no run yet" : card.model)").font(.caption).foregroundStyle(.secondary)
                    }
                }
                HStack {
                    MetricTile(title: "Runs", value: "\(card.runs.count)", systemImage: "play.circle")
                    MetricTile(title: "Cost", value: card.cost.dollars, systemImage: "dollarsign.circle")
                }
                if let active, card.state == .working {
                    section("Working now") {
                        Text(active.task.map { "On \($0)" } ?? "On a fix").font(.callout)
                        ForEach(Array(active.tools.suffix(6).enumerated()), id: \.offset) { _, event in
                            Text("\(event.tool ?? "") \(event.detail ?? "")").font(.caption.monospaced()).lineLimit(1)
                        }
                    }
                }
                if card.role == .worker { workerSections }
                if card.role == .planner { plannerSections }
                if card.role == .reviewer { reviewerSections }
                section("Runs") {
                    if card.runs.isEmpty { Text("None yet").foregroundStyle(.secondary) }
                    ForEach(Array(card.runs.enumerated()), id: \.offset) { _, run in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(run.label.isEmpty ? run.agent : run.label).font(.callout.weight(.medium))
                            Text("\(run.numTurns) turns · \(run.inputTokens.compact) in · \(run.outputTokens.compact) out · \(run.cacheReadTokens.compact) cached · \(run.durationMs / 1000)s")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            RelativeTime(timestamp: run.at).font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                }
            }
            .padding(16)
        }
    }

    @ViewBuilder
    private var workerSections: some View {
        let passes = ticket.workLog.filter { $0.worker == card.id }
        if !card.tasks.isEmpty {
            section("Tasks") {
                ForEach(card.tasks) { task in
                    Label(
                        "\(task.id) · \(task.title)",
                        systemImage: ticket.tasksDone.contains(task.id) ? "checkmark.circle.fill" : "circle")
                }
            }
        }
        if !passes.isEmpty {
            section("Handed on") {
                ForEach(Array(passes.enumerated()), id: \.offset) { _, pass in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(pass.subtask ?? "Follow-up").font(.caption.weight(.semibold))
                        MarkdownText(text: pass.handoff.isEmpty ? pass.summary : pass.handoff).font(.callout)
                    }
                }
            }
        }
        let asked = ticket.qa.filter { $0.worker == card.id }
        if !asked.isEmpty { questionList(asked) }
    }

    @ViewBuilder
    private var plannerSections: some View {
        if !ticket.subtasks.isEmpty {
            section("Assigned") {
                ForEach(ticket.workerNames, id: \.self) { worker in
                    let tasks = ticket.subtasks.filter { $0.worker == worker }.map(\.id).joined(separator: ", ")
                    Text("\(worker): \(tasks)").font(.callout)
                }
            }
        }
        let answered = ticket.qa.filter { $0.answeredBy == "planner" }
        if !answered.isEmpty { questionList(answered) }
    }

    @ViewBuilder
    private var reviewerSections: some View {
        section("Rounds") {
            ForEach(Array(ticket.reviews.enumerated()), id: \.offset) { _, review in
                VStack(alignment: .leading, spacing: 2) {
                    Text("Round \(review.round) · \(review.verdict)").font(.callout.weight(.medium))
                    Text("\(review.comments.filter(\.isBlocking).count) blocking, \(review.comments.filter { !$0.isBlocking }.count) minor")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func questionList(_ entries: [QuestionEntry]) -> some View {
        section("Questions") {
            ForEach(Array(entries.enumerated()), id: \.offset) { _, entry in
                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.question).font(.callout)
                    Text(entry.answer.isEmpty ? "Waiting for an answer" : "\(entry.answeredBy): \(entry.answer)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            content()
        }
    }
}
