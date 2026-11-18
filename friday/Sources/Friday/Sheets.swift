import AppKit
import SwiftUI

/// The request, plan-first and base branch of a new ticket: `cw run <project> [--plan] [--base] "..."`.
struct NewTicketForm: View {
    @Environment(Store.self) private var store
    @AppStorage(Preference.planFirstByDefault) private var planFirstByDefault = false
    @State var project: String
    var compact = false
    var onStart: () -> Void = {}
    @State private var prompt = ""
    @State private var planFirst: Bool?
    @State private var base = ""

    private var plan: Bool { planFirst ?? planFirstByDefault }
    private var defaultBase: String { store.project(project)?.baseBranch ?? "main" }
    private var arguments: [String] {
        var result = ["run", project]
        if plan { result.append("--plan") }
        if !base.isEmpty && base != defaultBase { result += ["--base", base] }
        return result + [prompt.trimmingCharacters(in: .whitespacesAndNewlines)]
    }
    private var dirty: Bool { store.info(project)?.dirty == true }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !compact {
                Picker("Project", selection: $project) {
                    ForEach(store.config?.projects ?? []) { Text($0.name).tag($0.name) }
                }
            }
            TextField("What should change? For example: add CSV export of the monthly report", text: $prompt, axis: .vertical)
                .lineLimit(3...8)
                .textFieldStyle(.roundedBorder)
            HStack(spacing: 16) {
                Toggle("Plan first", isOn: Binding(get: { plan }, set: { planFirst = $0 }))
                    .help("The planner writes a plan with tasks for the workers, and you approve it first")
                HStack(spacing: 6) {
                    Text("From").foregroundStyle(.secondary)
                    TextField(defaultBase, text: $base).frame(width: 140).textFieldStyle(.roundedBorder)
                }
                Spacer()
                Button("Start", action: start)
                    .buttonStyle(.borderedProminent)
                    .disabled(prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || dirty)
                    .keyboardShortcut(.return, modifiers: .command)
            }
            if dirty {
                Label("The checkout has uncommitted changes: commit, stash or park them first.", systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
            Text("cw " + arguments.dropLast().joined(separator: " ") + " \"…\"")
                .font(.caption.monospaced())
                .foregroundStyle(.tertiary)
        }
    }

    private func start() {
        store.run("New ticket · \(prompt.prefix(40))", project: project, ticketId: nil, arguments)
        store.select(project: project)
        prompt = ""
        onStart()
    }
}

struct NewTicketSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let project: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New ticket").font(.title2.weight(.semibold))
            if let name = project ?? store.config?.projects.first?.name {
                NewTicketForm(project: name, onStart: { dismiss() })
            } else {
                Text("Add a project first.").foregroundStyle(.secondary)
            }
            HStack {
                Button("New project from an idea instead") { store.sheet = .newProject }.buttonStyle(.link)
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(22)
        .frame(width: 560)
    }
}

/// `cw new <name> "<idea>" --path <folder> [--stack] [--github private|public]`.
struct NewProjectSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var idea = ""
    @State private var folder = (NSHomeDirectory() as NSString).appendingPathComponent("Projects")
    @State private var stack = ""
    @State private var github = "none"

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New project from an idea").font(.title2.weight(.semibold))
            Text("cw creates the repository, and the planner plans the whole project from your idea. You approve the plan before any code is written.")
                .font(.callout)
                .foregroundStyle(.secondary)
            Form {
                TextField("Name", text: $name, prompt: Text("habits"))
                TextField("Idea", text: $idea, prompt: Text("A habit tracker REST API with FastAPI and PostgreSQL"), axis: .vertical)
                    .lineLimit(3...6)
                HStack {
                    TextField("Folder", text: $folder)
                    Button("Choose…") { if let chosen = chooseFolder() { folder = chosen } }
                }
                Picker("Stack", selection: $stack) {
                    Text("Default").tag("")
                    ForEach((store.config?.stacks.keys).map(Array.init)?.sorted() ?? [], id: \.self) { Text($0).tag($0) }
                }
                Picker("GitHub", selection: $github) {
                    Text("Local only").tag("none")
                    Text("Private repository").tag("private")
                    Text("Public repository").tag("public")
                }
                .pickerStyle(.segmented)
            }
            .formStyle(.grouped)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Create and plan") {
                    var arguments = ["new", name, idea, "--path", folder]
                    if !stack.isEmpty { arguments += ["--stack", stack] }
                    if github != "none" { arguments += ["--github", github] }
                    store.run("New project \(name)", project: name, ticketId: nil, arguments)
                    store.sidebar = .activity
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .disabled(name.isEmpty || idea.isEmpty || name.contains(" "))
            }
        }
        .padding(22)
        .frame(width: 600)
    }
}

/// `cw project add <path> [--name] [--host] [--onboard]`.
struct AddProjectSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var path = ""
    @State private var name = ""
    @State private var host = "auto"
    @State private var onboard = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add a repository").font(.title2.weight(.semibold))
            Form {
                HStack {
                    TextField("Folder", text: $path, prompt: Text("~/code/my-service"))
                    Button("Choose…") {
                        if let chosen = chooseFolder() {
                            path = chosen
                            if name.isEmpty { name = URL(fileURLWithPath: chosen).lastPathComponent }
                        }
                    }
                }
                TextField("Name", text: $name, prompt: Text("the folder's name"))
                Picker("Code host", selection: $host) {
                    Text("Detect").tag("auto")
                    Text("GitHub").tag("github")
                    Text("Bitbucket").tag("bitbucket")
                }
                .pickerStyle(.segmented)
                Toggle("Onboard it now (one agent run: stack, commands, differences from the team rules)", isOn: $onboard)
            }
            .formStyle(.grouped)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Add") {
                    var arguments = ["project", "add", path]
                    if !name.isEmpty { arguments += ["--name", name] }
                    if host != "auto" { arguments += ["--host", host] }
                    if onboard { arguments.append("--onboard") }
                    store.run("Add \(name.isEmpty ? path : name)", project: name, ticketId: nil, arguments)
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .disabled(path.isEmpty)
            }
        }
        .padding(22)
        .frame(width: 560)
    }
}

struct ProjectSettingsSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let name: String

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("\(name) settings").font(.title2.weight(.semibold))
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            .padding(18)
            Divider()
            if let project = store.project(name) {
                ProjectSettingsView(project: project)
            }
        }
        .frame(width: 820, height: 700)
    }
}

struct ConventionsSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let name: String

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("\(name) conventions").font(.title2.weight(.semibold))
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            .padding(18)
            Divider()
            if let project = store.project(name) {
                OnboardingView(project: project)
            }
        }
        .frame(width: 820, height: 700)
    }
}

@MainActor
func chooseFolder() -> String? {
    let panel = NSOpenPanel()
    panel.canChooseDirectories = true
    panel.canChooseFiles = false
    panel.canCreateDirectories = true
    return panel.runModal() == .OK ? panel.url?.path : nil
}
