import AppKit
import SwiftUI

/// First run: install cw, write its config, add the first projects.
struct SetupView: View {
    @Environment(Store.self) private var store
    @State private var repository = ""
    @State private var toolkitPath = ""
    @State private var githubRepo = "OWNER/nexttech-toolkit"

    var body: some View {
        @Bindable var store = store
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Set up FRIDAY").font(.largeTitle.bold())
                Text("FRIDAY drives `cw`, which runs the toolkit's agents with your own Claude Code login. Three steps, once per machine.")
                    .foregroundStyle(.secondary)
                Card(title: "1 · Install cw", systemImage: "terminal") {
                    TextField("This repository (contains backend/)", text: $repository)
                    HStack {
                        Button("Install with uv") {
                            store.run("Install cw", project: "", ticketId: nil, ["tool", "install", "--force", repository + "/backend"], using: CW(executable: "uv", home: ""))
                            store.cwExecutable = ("~/.local/bin/cw" as NSString).expandingTildeInPath
                        }
                        TextField("cw executable", text: $store.cwExecutable)
                    }
                }
                Card(title: "2 · Create the config", systemImage: "doc.badge.gearshape") {
                    TextField("Local clone of the toolkit", text: $toolkitPath)
                    TextField("Toolkit on GitHub (owner/name)", text: $githubRepo)
                    TextField("Config folder (empty = ~/.cw)", text: $store.cwHome)
                    Button("Create config") {
                        store.run("cw init", project: "", ticketId: nil, ["init", "--toolkit", toolkitPath, "--github-repo", githubRepo])
                    }
                }
                Card(title: "3 · Add your projects", systemImage: "folder.badge.plus") {
                    Text("After the config exists, use Add in the Projects panel. Each repository is registered; no agent runs until you ask for something.")
                        .foregroundStyle(.secondary)
                    Button("Reload") { Task { await store.refreshAll() } }.buttonStyle(.borderedProminent)
                }
                if !store.jobs.isEmpty {
                    Card(title: "Output", systemImage: "text.alignleft") {
                        ForEach(store.jobs.reversed()) { JobRow(job: $0) }
                    }
                }
            }
            .padding(28)
            .frame(maxWidth: 760, alignment: .leading)
        }
        .onAppear {
            repository = store.repositoryRoot ?? ""
            toolkitPath = repository.isEmpty ? "" : repository + "/toolkit"
        }
    }
}
