import AppKit
import SwiftUI

/// IDE layout: icon rail · projects and files · code or tickets · FRIDAY chat.
struct RootView: View {
    @Environment(Store.self) private var store
    @State private var openFiles: [String] = []
    @State private var activeFile: String?
    @State private var treeSelection: String?

    var body: some View {
        @Bindable var store = store
        VStack(spacing: 0) {
            topBar
            Divider()
            if store.needsSetup {
                SetupView()
            } else {
                HStack(spacing: 0) {
                    rail
                    Divider()
                    HSplitView {
                        if store.rail == .projects {
                            sidebar.frame(minWidth: 240, idealWidth: 290, maxWidth: 420)
                        }
                        center.frame(minWidth: 420, maxWidth: .infinity)
                        ChatPanel().frame(minWidth: 300, idealWidth: 360, maxWidth: 520)
                    }
                }
            }
        }
        .background(Theme.background)
        .sheet(isPresented: $store.showProjectSettings) {
            if let name = store.selectedProject, let project = store.project(name) {
                ProjectSheet(project: project)
            }
        }
        .onChange(of: treeSelection) { _, path in
            guard let path else { return }
            if !openFiles.contains(path) { openFiles.append(path) }
            activeFile = path
        }
        .onChange(of: store.selectedProject) {
            openFiles = []
            activeFile = nil
            treeSelection = nil
        }
    }

    // MARK: top bar and rail

    private var topBar: some View {
        @Bindable var store = store
        return HStack(spacing: 16) {
            Text("F . R . I . D . A . Y .").font(.system(.headline, design: .monospaced)).foregroundStyle(Theme.accent)
            Text(store.selectedProject ?? "All projects").font(.headline)
            if let next = store.tickets.first(where: { !$0.archived && !$0.running && $0.status.needsYou }) {
                let count = store.tickets.filter { !$0.archived && !$0.running && $0.status.needsYou }.count
                Button {
                    store.rail = .projects
                    store.selectedProject = next.project
                    store.selectedTicket = next.id
                } label: {
                    Label("\(count) waiting for you", systemImage: "bell.badge")
                        .font(.caption.bold())
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(.orange.opacity(0.2), in: Capsule())
                        .foregroundStyle(.orange)
                }
                .buttonStyle(.plain)
                .help("Open the next ticket that needs you")
            }
            Picker("", selection: $store.topTab) {
                ForEach(TopTab.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .frame(width: 200)
            if let error = store.lastError {
                Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red).lineLimit(1).font(.caption)
                    .onTapGesture { store.lastError = nil }
            }
            Spacer()
            Button { Task { await store.refreshAll() } } label: { Image(systemName: "arrow.clockwise") }
                .buttonStyle(.borderless).help("Refresh")
            Button { store.rail = .settings } label: { Image(systemName: "gearshape") }.buttonStyle(.borderless)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Theme.panel)
    }

    private var rail: some View {
        VStack(spacing: 6) {
            ForEach(Rail.allCases) { item in
                Button {
                    store.rail = item
                } label: {
                    VStack(spacing: 4) {
                        Image(systemName: item.icon).font(.title3)
                        Text(item.rawValue).font(.caption2)
                    }
                    .frame(width: 64, height: 54)
                    .background(store.rail == item ? Theme.accent.opacity(0.15) : .clear, in: RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(store.rail == item ? Theme.accent.opacity(0.6) : .clear))
                    .foregroundStyle(store.rail == item ? Theme.accent : .secondary)
                    .overlay(alignment: .topTrailing) {
                        if item == .activity, store.jobs.contains(where: \.running) {
                            Circle().fill(Theme.accent).frame(width: 7, height: 7).offset(x: -10, y: 8)
                        }
                    }
                }
                .buttonStyle(.plain)
            }
            Spacer()
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 6)
        .background(Theme.panel)
    }

    // MARK: sidebar: projects + files

    private var sidebar: some View {
        @Bindable var store = store
        return VSplitView {
            VStack(spacing: 0) {
                HStack {
                    Text("PROJECTS").font(.caption.bold()).tracking(1.5).foregroundStyle(.secondary)
                    Spacer()
                    Button { addProject() } label: { Label("Add", systemImage: "plus") }.controlSize(.small)
                }
                .padding(10)
                List(selection: Binding(get: { store.selectedProject ?? "" }, set: { store.selectedProject = $0.isEmpty ? nil : $0; store.selectedTicket = nil })) {
                    Text("All projects").tag("")
                    ForEach(store.config?.projects ?? []) { project in
                        ProjectRow(project: project, info: store.info(project.name)).tag(project.name)
                    }
                }
                .listStyle(.sidebar)
            }
            .frame(minHeight: 160, idealHeight: 260)
            VStack(spacing: 0) {
                HStack {
                    Text("FILES").font(.caption.bold()).tracking(1.5).foregroundStyle(.secondary)
                    Spacer()
                }
                .padding(10)
                if let root = treeRoot {
                    FileTreeView(root: root, selection: $treeSelection).id(root)
                } else {
                    Text("Select a project to browse its code.").font(.caption).foregroundStyle(.secondary).padding()
                    Spacer()
                }
            }
        }
        .background(Theme.panel)
    }

    private var treeRoot: String? {
        guard let name = store.selectedProject, let project = store.project(name) else { return nil }
        return (project.path as NSString).expandingTildeInPath
    }

    // MARK: center

    @ViewBuilder
    private var center: some View {
        if store.topTab == .usage {
            UsageView()
        } else {
            switch store.rail {
            case .activity: ActivityView()
            case .toolkit: ToolkitView()
            case .settings: SettingsView()
            case .projects: projectCenter
            }
        }
    }

    private var projectCenter: some View {
        VStack(spacing: 0) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 2) {
                    tab("Tickets", icon: "list.bullet.rectangle", active: activeFile == nil) { activeFile = nil }
                    ForEach(openFiles, id: \.self) { path in
                        tab((path as NSString).lastPathComponent, icon: "doc.text", active: activeFile == path, close: {
                            openFiles.removeAll { $0 == path }
                            if activeFile == path { activeFile = openFiles.last }
                        }) { activeFile = path }
                    }
                }
                .padding(.horizontal, 6)
            }
            .frame(height: 34)
            .background(Theme.panel)
            Divider()
            if let activeFile {
                VStack(spacing: 0) {
                    Text(activeFile).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(1).truncationMode(.head)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(6)
                    CodeView(path: activeFile)
                }
            } else if let name = store.selectedProject {
                TicketsHome(projectName: name)
            } else {
                OverviewView()
            }
        }
    }

    private func tab(_ title: String, icon: String, active: Bool, close: (() -> Void)? = nil, select: @escaping () -> Void) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.caption)
            Text(title).font(.callout).lineLimit(1)
            if let close {
                Button(action: close) { Image(systemName: "xmark").font(.caption2) }.buttonStyle(.borderless)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(active ? Theme.editor : .clear, in: RoundedRectangle(cornerRadius: 6))
        .overlay(alignment: .bottom) { if active { Rectangle().fill(Theme.accent).frame(height: 2) } }
        .contentShape(Rectangle())
        .onTapGesture(perform: select)
    }

    private func addProject() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = true
        panel.prompt = "Add"
        panel.message = "Choose one or more git repositories. FRIDAY registers them; no agent runs."
        guard panel.runModal() == .OK else { return }
        for url in panel.urls {
            store.run("Add \(url.lastPathComponent)", project: url.lastPathComponent, ticketId: nil, ["project", "add", url.path])
        }
    }
}

private struct ProjectRow: View {
    @Environment(Store.self) private var store
    var project: Project
    var info: ProjectInfo?

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(project.name).fontWeight(.semibold)
                Spacer()
                if (info?.running ?? 0) > 0 { ProgressView().controlSize(.mini) }
                if let waiting = info?.waiting, waiting > 0 {
                    Text("\(waiting)").font(.caption2.bold()).padding(.horizontal, 5).background(.orange.opacity(0.3), in: Capsule())
                        .help("Tickets waiting for you")
                }
                Button {
                    store.selectedProject = project.name
                    store.showProjectSettings = true
                } label: {
                    Image(systemName: "slider.horizontal.3")
                }
                .buttonStyle(.borderless)
                .help("Project settings and project map")
            }
            HStack(spacing: 6) {
                Text(info?.branch ?? project.baseBranch).font(.caption.monospaced())
                if let info {
                    Text(info.exists ? (info.dirty ? "dirty" : "clean") : "missing")
                        .font(.caption.bold())
                        .foregroundStyle(!info.exists ? .red : info.dirty ? .yellow : .green)
                }
                Spacer()
                Text(info?.lastCommit ?? "").font(.caption2).foregroundStyle(.secondary)
            }
            Text(info?.remoteUrl ?? project.path).font(.caption2.monospaced()).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
        }
        .padding(.vertical, 3)
    }
}

/// The selected project's tickets beside the selected ticket's details.
private struct TicketsHome: View {
    @Environment(Store.self) private var store
    var projectName: String

    var body: some View {
        @Bindable var store = store
        let all = store.tickets(in: projectName)
        let open = all.filter { !$0.archived }
        let history = all.filter(\.archived)
        HSplitView {
            List(selection: $store.selectedTicket) {
                Section("Open") {
                    if open.isEmpty {
                        HowItWorks()
                    }
                    ForEach(open) { ticket in
                        VStack(alignment: .leading, spacing: 4) {
                            StatusChip(status: ticket.status, running: ticket.running)
                            Text(ticket.title).lineLimit(2)
                        }
                        .padding(.vertical, 3)
                        .tag(Optional(ticket.id))
                    }
                }
                Section("History (\(history.count))") {
                    ForEach(history) { ticket in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(ticket.title).lineLimit(1).foregroundStyle(.secondary)
                            Text("\(ticket.status.label) · \(ticket.updatedAt.shortTimestamp)").font(.caption2).foregroundStyle(.tertiary)
                        }
                        .tag(Optional(ticket.id))
                    }
                }
            }
            .frame(minWidth: 200, idealWidth: 240, maxWidth: 320)
            if let id = store.selectedTicket {
                TicketDetailView(ticketId: id).id(id)
            } else {
                EmptyHint(title: projectName, message: "Pick a ticket, open a file from the tree, or talk to FRIDAY.", systemImage: "chevron.left.forwardslash.chevron.right")
            }
        }
    }
}

struct ActivityView: View {
    @Environment(Store.self) private var store

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text("Activity").font(.title2.bold())
                if store.jobs.isEmpty {
                    Text("Nothing has run in this window yet.").foregroundStyle(.secondary)
                }
                ForEach(store.jobs.reversed()) { JobRow(job: $0) }
            }
            .padding(20)
        }
    }
}

/// Project settings and onboarding decisions, opened from a project row.
private struct ProjectSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    var project: Project
    @State private var tab = 0

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(project.name).font(.title2.bold())
                Picker("", selection: $tab) {
                    Text("Settings").tag(0)
                    Text("Project map").tag(1)
                }
                .pickerStyle(.segmented)
                .frame(width: 240)
                Spacer()
                Button("Close") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            .padding(16)
            Divider()
            if tab == 0 {
                ProjectSettingsView(project: project)
            } else {
                OnboardingView(project: project)
            }
        }
        .frame(minWidth: 820, minHeight: 640)
    }
}

/// The four steps, shown where a project has no tickets yet.
struct HowItWorks: View {
    private let steps = [
        ("text.bubble", "Describe the task", "in the chat on the right. Turn on Plan first for bigger changes."),
        ("hammer", "FRIDAY works on a new branch", "in your checkout, so you see the changes in your IDE. Don't edit that project meanwhile."),
        ("checkmark.seal", "Check the result", "changed files are coloured in the file tree. Reply in the chat to adjust."),
        ("arrow.up.circle", "Open the PR", "one commit, reviewed, pushed. Nothing leaves your Mac before this."),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("No tickets yet").font(.headline)
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: step.0).foregroundStyle(Theme.accent).frame(width: 20)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("\(index + 1). \(step.1)").font(.callout.weight(.semibold))
                        Text(step.2).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(.vertical, 6)
    }
}
