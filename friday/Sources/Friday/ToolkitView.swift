import SwiftUI

struct ToolkitView: View {
    @Environment(Store.self) private var store

    var body: some View {
        ScrollView {
            if let toolkit = store.toolkit, let config = store.config {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Toolkit").font(.largeTitle.bold())
                        Text("\(toolkit.marketplace) v\(toolkit.version) · \(toolkit.path)")
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                        Text("One marketplace for the whole team: every project loads the same agents, skills and hooks. Always on: \((toolkit.alwaysPlugins + toolkit.alwaysExternalPlugins).joined(separator: ", ")).")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Card(title: "Which model each agent uses", systemImage: "cpu") {
                        Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 6) {
                            GridRow {
                                Text("Agent").font(.caption.bold())
                                Text("File default").font(.caption.bold())
                                ForEach(config.projects) { Text($0.name).font(.caption.bold()) }
                            }
                            ForEach(AgentRole.allCases) { role in
                                GridRow {
                                    Text(role.label)
                                    Text(toolkit.agents.first { $0.name == role.rawValue }?.model ?? "?").foregroundStyle(.secondary)
                                    ForEach(config.projects) { project in
                                        Text(project.profile.models[role])
                                            .font(.callout.monospaced())
                                            .foregroundStyle(modelColor(project.profile.models[role]))
                                    }
                                }
                            }
                        }
                        Text("Change a project's models in its Settings tab, or the defaults in Settings.").font(.caption).foregroundStyle(.secondary)
                    }
                    Card(title: "Agents", systemImage: "person.3") {
                        ForEach(toolkit.agents) { agent in
                            VStack(alignment: .leading, spacing: 3) {
                                HStack {
                                    Text(agent.name).fontWeight(.semibold)
                                    Text(agent.plugin).font(.caption).padding(.horizontal, 6).background(Theme.card, in: Capsule())
                                }
                                Text(agent.description).font(.callout)
                                Text("Tools: \(agent.tools.isEmpty ? "all (inherits)" : agent.tools.joined(separator: ", "))   Pinned skills: \(agent.skills.isEmpty ? "none" : agent.skills.joined(separator: ", "))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Divider()
                        }
                    }
                    Card(title: "Skills", systemImage: "book.closed") {
                        ForEach(toolkit.skills) { skill in
                            VStack(alignment: .leading, spacing: 3) {
                                HStack {
                                    Text(skill.name).fontWeight(.semibold)
                                    Text(skill.plugin).font(.caption).padding(.horizontal, 6).background(Theme.card, in: Capsule())
                                    if !skill.modelInvocable {
                                        Text("command: /\(skill.plugin):\(skill.name)").font(.caption.monospaced()).foregroundStyle(Theme.accent)
                                    }
                                }
                                Text(skill.description).font(.callout).foregroundStyle(.secondary)
                            }
                            Divider()
                        }
                    }
                    Card(title: "Stacks", systemImage: "square.stack.3d.up") {
                        ForEach(toolkit.stacks.keys.sorted(), id: \.self) { name in
                            let stack = toolkit.stacks[name]!
                            VStack(alignment: .leading, spacing: 2) {
                                Text(name).fontWeight(.semibold)
                                Text("Detected by: \(stack.markers.joined(separator: ", "))").font(.caption)
                                Text("Toolkit plugins: \(stack.toolkitPlugins.joined(separator: ", ")) · Pinned: \(stack.pinnedSkills.joined(separator: ", "))").font(.caption)
                                Text("External: \(stack.externalPlugins.joined(separator: ", "))").font(.caption)
                            }
                            .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(24)
            } else {
                ProgressView().padding(40)
            }
        }
    }

    private func modelColor(_ model: String) -> Color {
        if model.contains("opus") || model.contains("fable") { return .purple }
        if model.contains("haiku") { return .green }
        return Theme.accent
    }
}

struct UsageView: View {
    @Environment(Store.self) private var store

    private var projects: [String] { Array(Set(store.usage.map(\.project))).sorted() }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text("Usage").font(.largeTitle.bold())
                    Spacer()
                    Button("Refresh") { Task { await store.refreshUsage() } }
                }
                Text("Tokens per project and agent, from every ticket's run log. Cost is the CLI's list-price estimate; on a subscription it shows relative weight, not a bill.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                if store.usage.isEmpty {
                    EmptyHint(title: "No runs yet", message: "Usage appears after agents run.", systemImage: "chart.bar")
                }
                ForEach(projects, id: \.self) { project in
                    let rows = store.usage.filter { $0.project == project }
                    let maxTokens = max(rows.map { $0.inputTokens + $0.outputTokens }.max() ?? 1, 1)
                    Card(title: project, systemImage: "folder") {
                        ForEach(rows) { row in
                            HStack {
                                Text(row.role).frame(width: 90, alignment: .leading)
                                GeometryReader { geometry in
                                    RoundedRectangle(cornerRadius: 4)
                                        .fill(Theme.accent.opacity(0.6))
                                        .frame(width: geometry.size.width * CGFloat(row.inputTokens + row.outputTokens) / CGFloat(maxTokens))
                                }
                                .frame(height: 12)
                                Text("\(row.runs) runs · \(row.inputTokens.compact) in · \(row.outputTokens.compact) out · \(row.cacheReadTokens.compact) cached · $\(String(format: "%.2f", row.costUsd))")
                                    .font(.caption.monospaced())
                                    .frame(width: 380, alignment: .trailing)
                            }
                        }
                    }
                }
            }
            .padding(24)
        }
        .task { await store.refreshUsage() }
    }
}
