import SwiftUI

/// The conversation beside the code: the selected ticket as a chat, and the box that talks to FRIDAY.
struct ChatPanel: View {
    @Environment(Store.self) private var store
    @State private var ticket: Ticket?
    @State private var prompt = ""
    @State private var planFirst = false

    private var projectName: String? { store.selectedProject ?? store.config?.projects.first?.name }
    private var summary: TicketSummary? { store.tickets.first { $0.id == store.selectedTicket } }
    private var replying: Bool {
        guard let summary else { return false }
        return !summary.archived && summary.status != .done && summary.status != .abandoned
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            if let ticket { NextStepCard(ticket: ticket, compact: true).padding(10) }
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(Array(messages.enumerated()), id: \.offset) { _, message in
                            bubble(message)
                        }
                        if let id = store.selectedTicket, let job = store.job(for: id), job.running {
                            pipelineBubble(job)
                        }
                        Color.clear.frame(height: 1).id("bottom")
                    }
                    .padding(12)
                }
                .onChange(of: messages.count) { proxy.scrollTo("bottom") }
                .onChange(of: store.selectedTicket) { proxy.scrollTo("bottom") }
            }
            Divider()
            composer
        }
        .background(Theme.panel)
        .task(id: "\(store.selectedTicket ?? "")|\(summary?.updatedAt ?? "")") {
            ticket = store.selectedTicket == nil ? nil : await store.ticket(store.selectedTicket!)
        }
    }

    private var header: some View {
        HStack {
            Text("FRIDAY").font(.callout.bold()).foregroundStyle(Theme.accent)
                .padding(.horizontal, 10).padding(.vertical, 4)
                .background(Theme.accent.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
            Text(replying ? "Replying to this ticket" : "New ticket in \(projectName ?? "…")")
                .font(.caption.weight(.semibold))
                .foregroundStyle(replying ? Theme.accent : .secondary)
                .lineLimit(1)
            Spacer()
            Button {
                store.selectedTicket = nil
            } label: {
                Image(systemName: "square.and.pencil")
            }
            .buttonStyle(.borderless)
            .help("New conversation (new ticket)")
        }
        .padding(10)
    }

    struct Message {
        enum Kind { case user, friday, pipeline }
        var kind: Kind
        var text: String
        var at: String
        var actions: [(String, [String])] = []
    }

    private var messages: [Message] {
        guard let ticket else {
            let name = projectName ?? "a project"
            var welcome = [Message(kind: .friday, text: "Focus: \(name). Tell me what to do. Small tasks go straight to a worker; switch on Plan for bigger ones and I'll come back with a plan first.", at: "")]
            for summary in store.tickets.filter({ $0.project == projectName && !$0.archived }).prefix(5) {
                welcome.append(Message(kind: .pipeline, text: "\(summary.status.label) · \(summary.title)", at: summary.updatedAt))
            }
            return welcome
        }
        var result: [Message] = ticket.prompts.map { Message(kind: .user, text: $0.text, at: $0.at) }
        let planAt = ticket.runs.last { $0.role == "planner" }?.at ?? ticket.prompts.first?.at ?? ""
        if !ticket.questions.isEmpty {
            result.append(Message(kind: .friday, text: "Before I plan, I need:\n" + ticket.questions.map { "• \($0)" }.joined(separator: "\n"), at: planAt))
        }
        if !ticket.plan.isEmpty {
            let subtasks = ticket.subtasks.map { "\((ticket.tasksDone ?? []).contains($0.id) ? "✓" : "○") \($0.id) · \($0.title)" }.joined(separator: "\n")
            result.append(Message(kind: .friday, text: "Plan:\n\(ticket.plan)\n\n\(subtasks)", at: planAt))
        }
        for work in ticket.workLog {
            result.append(Message(kind: .friday, text: "\(work.subtask.map { "[\($0)] " } ?? "")\(work.summary)", at: work.at))
        }
        for review in ticket.reviews {
            let comments = review.comments.map { "• \($0.path):\($0.line) (\($0.severity)) \($0.body)" }.joined(separator: "\n")
            result.append(Message(kind: .pipeline, text: "Review round \(review.round) · \(review.verdict)\n\(review.summary)\n\(comments)", at: review.at))
        }
        result.sort { $0.at < $1.at }
        return result
    }

    @ViewBuilder
    private func bubble(_ message: Message) -> some View {
        switch message.kind {
        case .user:
            HStack {
                Spacer(minLength: 40)
                Text(message.text).textSelection(.enabled)
                    .padding(10)
                    .background(Theme.accent.opacity(0.22), in: RoundedRectangle(cornerRadius: 10))
            }
        case .friday:
            Text(message.text).textSelection(.enabled)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
        case .pipeline:
            VStack(alignment: .leading, spacing: 6) {
                Text(message.text).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                if !message.actions.isEmpty, let ticket {
                    HStack {
                        ForEach(message.actions, id: \.0) { action in
                            Button(action.0) { store.run("\(action.0) · \(ticket.title.prefix(40))", project: ticket.project, ticketId: ticket.id, action.1) }
                                .buttonStyle(.borderedProminent)
                                .controlSize(.small)
                                .disabled(summary?.running == true)
                        }
                    }
                }
            }
            .padding(.leading, 10)
            .overlay(alignment: .leading) { Rectangle().fill(Theme.accent).frame(width: 2) }
        }
    }

    private func pipelineBubble(_ job: Job) -> some View {
        HStack(alignment: .top, spacing: 8) {
            ProgressView().controlSize(.small)
            Text(job.log.split(separator: "\n").suffix(4).joined(separator: "\n"))
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
        }
        .padding(.leading, 10)
        .overlay(alignment: .leading) { Rectangle().fill(Theme.accent).frame(width: 2) }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 6) {
            if replying, let summary {
                HStack {
                    Text("Replying to \(summary.title)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    Spacer()
                    Button("New ticket") { store.selectedTicket = nil }.buttonStyle(.link).font(.caption)
                }
            }
            TextField("Talk to FRIDAY… (focus: \(projectName ?? "none")) ↩ send, ⌥↩ new line", text: $prompt, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...8)
                .padding(10)
                .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
                .onSubmit(send)
            HStack {
                if !replying {
                    Toggle("Plan first", isOn: $planFirst).toggleStyle(.switch).controlSize(.mini)
                        .help("Off: a worker starts right away (best for small tasks). On: the planner writes a plan you approve first.")
                    Text(planFirst ? "plan → you approve → work" : "straight to a worker").font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                Button(replying ? "Reply" : "Start ticket", action: send)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .disabled(prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || projectName == nil)
            }
        }
        .padding(10)
    }

    private func send() {
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let projectName else { return }
        if replying, let summary {
            store.run("Follow-up · \(summary.title.prefix(40))", project: summary.project, ticketId: summary.id, ["run", summary.id, text])
        } else {
            var arguments = ["run", projectName, text]
            if planFirst { arguments.append("--plan") }
            store.run(String(text.prefix(50)), project: projectName, ticketId: nil, arguments)
        }
        prompt = ""
    }
}
