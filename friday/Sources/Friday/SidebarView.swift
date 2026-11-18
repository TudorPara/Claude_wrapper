import SwiftUI

struct SidebarView: View {
    @Environment(Store.self) private var store
    @Environment(\.isSnapshot) private var isSnapshot
    @State private var removing: String?

    var body: some View {
        @Bindable var store = store
        List(selection: $store.sidebar) {
            Section("Projects") {
                ForEach(store.config?.projects ?? []) { project in
                    ProjectRow(project: project, info: store.info(project.name))
                        .tag(SidebarItem.project(project.name))
                        .contextMenu { menu(for: project) }
                }
                Button {
                    store.sheet = .addProject
                } label: {
                    Label("Add a repository", systemImage: "plus")
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                Button {
                    store.sheet = .newProject
                } label: {
                    Label("New project from an idea", systemImage: "sparkles")
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
            }
            Section("Workspace") {
                Label {
                    HStack {
                        Text("Activity")
                        Spacer()
                        if !store.runningJobs.isEmpty { Pill(text: "\(store.runningJobs.count)", color: .blue) }
                    }
                } icon: {
                    Image(systemName: "waveform.path.ecg")
                }
                .tag(SidebarItem.activity)
                Label("Usage", systemImage: "chart.bar").tag(SidebarItem.usage)
                Label("Toolkit", systemImage: "square.stack.3d.up").tag(SidebarItem.toolkit)
                Label {
                    HStack {
                        Text("Settings")
                        Spacer()
                        if !store.doctorProblems.isEmpty { Pill(text: "\(store.doctorProblems.count)", color: .orange) }
                    }
                } icon: {
                    Image(systemName: "gearshape")
                }
                .tag(SidebarItem.settings)
            }
        }
        .listStyle(.sidebar)
        .scrollContentBackground(isSnapshot ? .hidden : .automatic)
        .background(isSnapshot ? Color(nsColor: .controlBackgroundColor) : .clear)
        .confirmationDialog(
            "Unregister \(removing ?? "")?", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } })
        ) {
            Button("Unregister", role: .destructive) {
                if let name = removing { store.run("Remove \(name)", project: name, ticketId: nil, ["project", "remove", name]) }
                removing = nil
            }
        } message: {
            Text("cw forgets the project. The repository and its tickets stay on disk.")
        }
    }

    @ViewBuilder
    private func menu(for project: Project) -> some View {
        Button("New ticket") { store.sheet = .newTicket(project: project.name) }
        Button("Project settings") { store.sheet = .projectSettings(project.name) }
        Divider()
        Button("Map with the onboarder") { store.run("Onboard \(project.name)", project: project.name, ticketId: nil, ["project", "onboard", project.name]) }
        Button("Rebuild the code map") { store.run("Map \(project.name)", project: project.name, ticketId: nil, ["project", "map", project.name, "--json"]) }
        Button("Wire the toolkit into the repository") { store.run("Wire \(project.name)", project: project.name, ticketId: nil, ["project", "wire", project.name]) }
        Divider()
        Button("Unregister", role: .destructive) { removing = project.name }
    }
}

private struct ProjectRow: View {
    let project: Project
    let info: ProjectInfo?

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: info?.exists == false ? "folder.badge.questionmark" : "folder")
                .foregroundStyle(info?.exists == false ? .red : .secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(project.name).lineLimit(1)
                if let info {
                    Text(info.branch + (info.dirty ? " ●" : ""))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer()
            if let info, info.running > 0 {
                ProgressView().controlSize(.mini).help("\(info.running) running")
            } else if let info, info.waiting > 0 {
                Pill(text: "\(info.waiting)", color: .orange).help("\(info.waiting) waiting for you")
            }
        }
    }
}
