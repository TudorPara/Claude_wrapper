import SwiftUI

/// The window: projects and workspace pages on the left; a project's tickets and the open ticket on the right.
struct RootView: View {
    @Environment(Store.self) private var store
    @Environment(\.isSnapshot) private var isSnapshot

    var body: some View {
        @Bindable var store = store
        Group {
            if store.needsSetup {
                SetupView()
            } else if isSnapshot {
                HStack(spacing: 0) {
                    SidebarView().frame(width: 240)
                    Divider()
                    detail
                }
            } else {
                NavigationSplitView {
                    SidebarView()
                        .navigationSplitViewColumnWidth(min: 210, ideal: 240, max: 320)
                } detail: {
                    detail
                }
            }
        }
        .sheet(item: $store.sheet) { route in
            switch route {
            case .newTicket(let project): NewTicketSheet(project: project)
            case .newProject: NewProjectSheet()
            case .addProject: AddProjectSheet()
            case .projectSettings(let name): ProjectSettingsSheet(name: name)
            case .conventions(let name): ConventionsSheet(name: name)
            }
        }
        .alert("cw", isPresented: Binding(get: { store.lastError != nil }, set: { if !$0 { store.lastError = nil } })) {
            Button("OK") { store.lastError = nil }
        } message: {
            Text(store.lastError ?? "")
        }
    }

    @ViewBuilder
    private var detail: some View {
        switch store.sidebar {
        case .project(let name):
            ProjectWorkspace(project: name)
        case .activity:
            ActivityView()
        case .usage:
            UsageView()
        case .toolkit:
            ToolkitView()
        case .settings:
            SettingsView()
        case nil:
            ContentUnavailableView("Add a project", systemImage: "folder.badge.plus", description: Text("Register a repository you have, or start a project from an idea."))
        }
    }
}

/// A project's tickets beside the open ticket, or the project's home when no ticket is open.
struct ProjectWorkspace: View {
    @Environment(Store.self) private var store
    let project: String

    var body: some View {
        HStack(spacing: 0) {
            TicketListView(project: project)
                .frame(width: 290)
            Divider()
            Group {
                if let id = store.selectedTicket, store.summary(id)?.project == project {
                    TicketDetailView(ticketId: id).id(id)
                } else {
                    ProjectHomeView(project: project)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .navigationTitle(project)
        .navigationSubtitle(store.info(project).map { "\($0.branch)\($0.dirty ? " · uncommitted changes" : "")" } ?? "")
        .toolbar { WorkspaceToolbar(project: project) }
    }
}

struct WorkspaceToolbar: ToolbarContent {
    @Environment(Store.self) private var store
    let project: String?

    var body: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            if !store.runningJobs.isEmpty {
                Button {
                    store.sidebar = .activity
                } label: {
                    Label("\(store.runningJobs.count) running", systemImage: "gearshape.2")
                }
                .help("Commands FRIDAY is running")
            }
            Button {
                store.sheet = .newTicket(project: project)
            } label: {
                Label("New ticket", systemImage: "plus")
            }
            .help("Start a ticket (⌘N)")
            Button {
                Task { await store.refreshAll() }
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
        }
    }
}
