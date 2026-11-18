import SwiftUI

/// What happens next, and the `cw` commands the ticket's status allows.
struct NextStepBar: View {
    @Environment(Store.self) private var store
    let ticket: Ticket
    let active: ActiveRun?
    let running: Bool
    @State private var ciStatus: String?

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: running ? "gearshape.2" : Theme.symbol(for: ticket.status))
                .foregroundStyle(running ? .blue : Theme.color(for: ticket.status))
            VStack(alignment: .leading, spacing: 1) {
                Text(ticket.nextStep(active: active)).font(.callout).lineLimit(2)
                if let started = active?.startedAt {
                    Text(started, style: .timer).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                } else if let ciStatus {
                    Text(ciStatus).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Spacer()
            ForEach(ticket.actions(running: running)) { action in
                button(for: action)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .background(.bar)
    }

    @ViewBuilder
    private func button(for action: TicketAction) -> some View {
        let label = Label(action.title, systemImage: action.systemImage)
        if action.style == .primary && action.blockedReason == nil {
            Button { perform(action) } label: { label }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.return, modifiers: .command)
        } else {
            Button { perform(action) } label: { label }
                .disabled(action.blockedReason != nil)
                .help(action.blockedReason ?? action.title)
        }
    }

    private func perform(_ action: TicketAction) {
        if action.arguments.first == "ci" {
            Task {
                let output = try? await store.cw.output(action.arguments)
                ciStatus = output.flatMap { String(data: $0, encoding: .utf8) }?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "Could not read the CI status"
            }
            return
        }
        store.run(action.title, project: ticket.project, ticketId: ticket.id, action.arguments)
    }
}

/// Answers, plan feedback and follow-ups: `cw run <ticket> "..."`, typed or dictated.
struct Composer: View {
    @Environment(Store.self) private var store
    let ticket: Ticket
    let running: Bool
    @State private var text = ""
    @FocusState private var focused: Bool

    private var mode: ComposerMode { ticket.composerMode }

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(mode.placeholder, text: $text, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...6)
                .focused($focused)
                .padding(8)
                .background(Theme.card, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(focused ? Color.accentColor : Theme.border))
                .onSubmit(send)
                .disabled(mode == .closed)
            Button {
                store.voice.toggle()
            } label: {
                Image(systemName: store.voice.listening ? "mic.fill" : "mic")
            }
            .help(store.voice.problem ?? "Dictate")
            .disabled(mode == .closed)
            Button(mode.sendLabel, action: send)
                .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || running || mode == .closed)
                .help(running ? "Wait until the agent finishes, or stop it" : "cw run \(ticket.id) \"…\"")
        }
        .padding(.horizontal, 20)
        .padding(.bottom, 14)
        .padding(.top, 6)
        .onChange(of: store.voice.transcript) { _, transcript in
            if store.voice.listening, !transcript.isEmpty { text = transcript }
        }
    }

    private func send() {
        let prompt = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty, !running, mode != .closed else { return }
        store.voice.cancel()
        store.run("\(mode.sendLabel) · \(ticket.title.prefix(40))", project: ticket.project, ticketId: ticket.id, ["run", ticket.id, prompt])
        text = ""
    }
}
