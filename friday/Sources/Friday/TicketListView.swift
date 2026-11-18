import SwiftUI

/// A project's tickets: running first, then what needs you, open pull requests, and what is closed.
struct TicketListView: View {
    @Environment(Store.self) private var store
    let project: String
    @State private var search = ""
    @State private var showClosed = false

    private var tickets: [TicketSummary] {
        let all = store.tickets(in: project).sorted { $0.updatedAt > $1.updatedAt }
        guard !search.isEmpty else { return all }
        return all.filter { $0.title.localizedCaseInsensitiveContains(search) || $0.id.contains(search) }
    }

    private var groups: [(String, [TicketSummary])] {
        let open = tickets.filter { !$0.archived }
        return [
            ("Running", open.filter(\.running)),
            ("Needs you", open.filter(\.needsYou)),
            ("Pull requests", open.filter { !$0.running && $0.status == .prOpen }),
            ("In progress", open.filter { !$0.running && !$0.needsYou && $0.status != .prOpen }),
        ].filter { !$0.1.isEmpty }
    }

    var body: some View {
        @Bindable var store = store
        List(selection: $store.selectedTicket) {
            Button {
                store.selectedTicket = nil
            } label: {
                Label("Project overview", systemImage: "house")
                    .fontWeight(store.selectedTicket == nil ? .semibold : .regular)
            }
            .buttonStyle(.plain)
            ForEach(groups, id: \.0) { title, items in
                Section(title) {
                    ForEach(items) { TicketRow(summary: $0).tag($0.id) }
                }
            }
            let closed = tickets.filter(\.archived)
            if !closed.isEmpty {
                Section(isExpanded: $showClosed) {
                    ForEach(closed) { TicketRow(summary: $0).tag($0.id) }
                } header: {
                    Text("Closed (\(closed.count))")
                }
            }
        }
        .listStyle(.inset)
        .safeAreaInset(edge: .top) {
            TextField("Search tickets", text: $search)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal, 10)
                .padding(.top, 8)
        }
        .overlay {
            if store.tickets(in: project).isEmpty {
                ContentUnavailableView {
                    Label("No tickets yet", systemImage: "tray")
                } description: {
                    Text("Describe a change and an agent starts on it.")
                } actions: {
                    Button("New ticket") { store.sheet = .newTicket(project: project) }
                }
            }
        }
    }
}

private struct TicketRow: View {
    let summary: TicketSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(summary.title).font(.body.weight(.medium)).lineLimit(2)
            HStack(spacing: 6) {
                StatusBadge(status: summary.status, running: summary.running)
                if summary.prUrl != nil { Image(systemName: "arrow.triangle.pull").font(.caption).foregroundStyle(.secondary) }
                Spacer()
                RelativeTime(timestamp: summary.updatedAt).font(.caption).foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 3)
        .help(summary.error ?? summary.id)
    }
}
