import SwiftUI

struct OverviewView: View {
    @Environment(Store.self) private var store

    private var open: [TicketSummary] { store.tickets.filter { !$0.archived } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Overview").font(.largeTitle.bold())
                if store.config?.projects.isEmpty ?? true {
                    Card(title: "Start here", systemImage: "sparkles") {
                        Text("Add a git repository with Add in the Projects panel. Then pick it, and tell FRIDAY what to do in the chat on the right.")
                            .foregroundStyle(.secondary)
                        HowItWorks()
                    }
                }
                HStack(spacing: 12) {
                    stat("Open tickets", open.count, .primary)
                    stat("Waiting for you", open.filter { $0.status.needsYou && !$0.running }.count, .orange)
                    stat("Running", open.filter(\.running).count, Theme.accent)
                    stat("PRs open", open.filter { $0.status == .prOpen }.count, .green)
                }
                if !store.jobs.isEmpty {
                    Card(title: "Activity", systemImage: "waveform.path.ecg") {
                        ForEach(store.jobs.reversed().prefix(6)) { job in
                            JobRow(job: job)
                        }
                    }
                }
                Card(title: "Open tickets", systemImage: "tray.full") {
                    if open.isEmpty {
                        Text("Nothing open. Pick a project and tell FRIDAY what to do.").foregroundStyle(.secondary)
                    }
                    ForEach(open) { ticket in
                        Button {
                            store.selectedProject = ticket.project
                            store.selectedTicket = ticket.id
                        } label: {
                            HStack {
                                StatusChip(status: ticket.status, running: ticket.running)
                                Text(ticket.project).foregroundStyle(.secondary).frame(width: 140, alignment: .leading)
                                Text(ticket.title).lineLimit(1)
                                Spacer()
                                Text(ticket.updatedAt.shortTimestamp).font(.caption).foregroundStyle(.secondary)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        Divider()
                    }
                }
            }
            .padding(24)
        }
    }

    private func stat(_ title: String, _ value: Int, _ color: Color) -> some View {
        VStack(alignment: .leading) {
            Text("\(value)").font(.system(size: 30, weight: .bold, design: .rounded)).foregroundStyle(color)
            Text(title).font(.caption).foregroundStyle(.secondary)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 12))
    }
}

struct JobRow: View {
    @Environment(Store.self) private var store
    var job: Job
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            LogView(text: job.log)
        } label: {
            HStack {
                if job.running {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: job.exitCode == 0 ? "checkmark.circle.fill" : "xmark.octagon.fill")
                        .foregroundStyle(job.exitCode == 0 ? .green : .red)
                }
                Text(job.title).lineLimit(1)
                Spacer()
                Text(job.log.split(separator: "\n").last.map(String.init) ?? "")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .frame(maxWidth: 380, alignment: .trailing)
                if job.running {
                    Button("Stop") { store.stop(job) }.controlSize(.small)
                }
            }
        }
    }
}

struct LogView: View {
    var text: String

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                Text(text.isEmpty ? "Waiting for output…" : text)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                Color.clear.frame(height: 1).id("end")
            }
            .frame(minHeight: 120, maxHeight: 260)
            .background(Color.black.opacity(0.25), in: RoundedRectangle(cornerRadius: 8))
            .onChange(of: text) { proxy.scrollTo("end", anchor: .bottom) }
        }
    }
}
