import AppKit
import SwiftUI

/// A project at a glance: its checkout, what its tickets are doing, a box to start the next one, and its tools.
struct ProjectHomeView: View {
    @Environment(Store.self) private var store
    let project: String

    private var tickets: [TicketSummary] { store.tickets(in: project) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                HStack(spacing: 10) {
                    MetricTile(title: "Running", value: "\(tickets.filter(\.running).count)", systemImage: "gearshape.2")
                    MetricTile(title: "Need you", value: "\(tickets.filter(\.needsYou).count)", systemImage: "hand.raised")
                    MetricTile(title: "Open PRs", value: "\(tickets.filter { $0.status == .prOpen }.count)", systemImage: "arrow.triangle.pull")
                    MetricTile(title: "Spent", value: store.cost(of: project).dollars, systemImage: "dollarsign.circle")
                }
                Block(title: "Start a ticket", systemImage: "plus.bubble", tint: .blue) {
                    NewTicketForm(project: project, compact: true)
                }
                attention
                tools
            }
            .padding(24)
            .frame(maxWidth: 920, alignment: .leading)
        }
    }

    private var header: some View {
        let info = store.info(project)
        return VStack(alignment: .leading, spacing: 6) {
            Text(project).font(.largeTitle.weight(.semibold))
            HStack(spacing: 14) {
                if let info {
                    Label(info.branch, systemImage: "arrow.triangle.branch")
                    Label(info.dirty ? "Uncommitted changes" : "Clean", systemImage: info.dirty ? "circle.fill" : "checkmark.circle")
                        .foregroundStyle(info.dirty ? .orange : .secondary)
                    if !info.lastCommit.isEmpty { Label("Last commit \(info.lastCommit)", systemImage: "clock") }
                    if let url = remoteURL(info.remoteUrl) { Link(destination: url) { Label("Remote", systemImage: "link") } }
                }
            }
            .font(.callout)
            .foregroundStyle(.secondary)
            if let path = info?.path ?? store.project(project)?.path {
                Button {
                    NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: path)
                } label: {
                    Text(path).font(.caption.monospaced())
                }
                .buttonStyle(.link)
                .help("Show in Finder")
            }
        }
    }

    @ViewBuilder
    private var attention: some View {
        let waiting = tickets.filter { $0.needsYou || $0.running }
        if !waiting.isEmpty {
            Block(title: "Now", systemImage: "bolt") {
                ForEach(waiting) { summary in
                    Button {
                        store.selectedTicket = summary.id
                    } label: {
                        HStack {
                            Text(summary.title)
                            Spacer()
                            StatusBadge(status: summary.status, running: summary.running)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var tools: some View {
        let info = store.info(project)
        return Block(title: "Project tools", systemImage: "wrench.and.screwdriver") {
            HStack {
                Label(info?.conventions == true ? "Onboarded: conventions recorded" : "Not onboarded yet", systemImage: info?.conventions == true ? "checkmark.seal" : "seal")
                    .foregroundStyle(info?.conventions == true ? .green : .secondary)
                Spacer()
                if info?.conventions == true {
                    Button("Conventions") { store.sheet = .conventions(project) }
                }
                Button(info?.conventions == true ? "Onboard again" : "Onboard") {
                    store.run("Onboard \(project)", project: project, ticketId: nil, ["project", "onboard", project])
                }
                .help("The onboarder maps the stack, the commands and how the project differs from the team rules (one agent run)")
            }
            Divider()
            HStack {
                Button("Rebuild code map") { store.run("Map \(project)", project: project, ticketId: nil, ["project", "map", project, "--json"]) }
                    .help("The planner's map of folders, classes and functions; no agent runs")
                Button("Wire the toolkit") { store.run("Wire \(project)", project: project, ticketId: nil, ["project", "wire", project]) }
                    .help("Writes the toolkit into the repository's .claude/settings.json")
                Spacer()
                Button("Settings") { store.sheet = .projectSettings(project) }
            }
        }
    }

    private func remoteURL(_ remote: String) -> URL? {
        guard !remote.isEmpty else { return nil }
        if remote.hasPrefix("git@") {
            let path = remote.dropFirst(4).replacingOccurrences(of: ":", with: "/").replacingOccurrences(of: ".git", with: "")
            return URL(string: "https://\(path)")
        }
        return URL(string: remote.replacingOccurrences(of: ".git", with: ""))
    }
}
