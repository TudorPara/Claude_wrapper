import SwiftUI

/// What the onboarder found, with one decision per difference between the project and the team rules.
struct OnboardingView: View {
    @Environment(Store.self) private var store
    var project: Project
    @State private var lines: [String] = []
    @State private var raw = false
    @State private var dirty = false

    private struct Difference: Identifiable {
        var id: String
        var title: String
        var theirs: String
        var ours: String
        var example: String
        var decisionLine: Int
        var decision: String
    }

    private static let decisions = ["undecided", "keep theirs", "migrate to ours", "later"]

    private var url: URL {
        let home = store.cwHome.isEmpty ? "~/.cw" : store.cwHome
        return URL(fileURLWithPath: (home as NSString).expandingTildeInPath).appendingPathComponent("projects/\(project.name)/conventions.md")
    }

    private var summary: [String] {
        lines.filter { $0.hasPrefix("- Stack:") || $0.hasPrefix("- Architecture:") || $0.hasPrefix("- Tests:") || $0.hasPrefix("- Setup:") || $0.hasPrefix("- Lint:") || $0.hasPrefix("- Test:") }
            .map { String($0.dropFirst(2)) }
    }

    private var warnings: [String] {
        guard let start = lines.firstIndex(of: "## Warnings") else { return [] }
        return lines[(start + 1)...].prefix { !$0.hasPrefix("## ") }.filter { $0.hasPrefix("- ") }.map { String($0.dropFirst(2)) }
    }

    private var differences: [Difference] {
        var result: [Difference] = []
        for (index, line) in lines.enumerated() where line.hasPrefix("### ") {
            var item = Difference(id: "\(index)", title: String(line.dropFirst(4)), theirs: "", ours: "", example: "", decisionLine: -1, decision: "undecided")
            for offset in (index + 1)..<min(index + 8, lines.count) {
                let value = lines[offset]
                if value.hasPrefix("### ") { break }
                if value.hasPrefix("- Theirs: ") { item.theirs = String(value.dropFirst(10)) }
                if value.hasPrefix("- Ours: ") { item.ours = String(value.dropFirst(8)) }
                if value.hasPrefix("- Example: ") { item.example = String(value.dropFirst(11)) }
                if value.hasPrefix("- Decision: ") {
                    item.decisionLine = offset
                    item.decision = String(value.dropFirst(12))
                }
            }
            if item.decisionLine >= 0 { result.append(item) }
        }
        return result
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Optional. Mapping runs the onboarder agent once (it costs tokens): it reads the repository without changing it, works out the lint and test commands, and lists where the code differs from the team rules. Useful for an unfamiliar repository without a CLAUDE.md, or before migrating a project to the team rules. Your decisions are stored in cw's own folder, never in the repository, and every agent reads them.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                HStack {
                    Button(lines.isEmpty ? "Map this project" : "Map again") {
                        store.run("Map \(project.name)", project: project.name, ticketId: nil, ["project", "onboard", project.name])
                    }
                    Button("Write team settings to repo") {
                        store.run("Wire \(project.name)", project: project.name, ticketId: nil, ["project", "wire", project.name])
                    }
                    .help("Writes .claude/settings.json in the repository so teammates' Claude Code uses the same toolkit. Nothing is written unless you click this.")
                    Toggle("Edit raw file", isOn: $raw).toggleStyle(.switch).controlSize(.small)
                    Spacer()
                    Button("Save decisions") { save() }.buttonStyle(.borderedProminent).disabled(!dirty)
                }
                if lines.isEmpty {
                    EmptyHint(title: "Not mapped", message: "Nothing to do here unless you want the map. Agents work fine without it.", systemImage: "map")
                } else if raw {
                    TextEditor(text: Binding(get: { lines.joined(separator: "\n") }, set: { lines = $0.components(separatedBy: "\n"); dirty = true }))
                        .font(.system(.body, design: .monospaced))
                        .frame(minHeight: 420)
                } else {
                    Card(title: "Map", systemImage: "map") {
                        ForEach(summary, id: \.self) { Text($0).textSelection(.enabled) }
                    }
                    if !warnings.isEmpty {
                        Card(title: "Warnings", systemImage: "exclamationmark.triangle") {
                            ForEach(warnings, id: \.self) { Text("• \($0)").font(.callout) }
                        }
                    }
                    ForEach(differences) { difference in
                        Card(title: difference.title, systemImage: "arrow.left.arrow.right") {
                            HStack(alignment: .top, spacing: 16) {
                                VStack(alignment: .leading) {
                                    Text("Theirs").font(.caption.bold()).foregroundStyle(.secondary)
                                    Text(difference.theirs)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                VStack(alignment: .leading) {
                                    Text("Ours").font(.caption.bold()).foregroundStyle(Theme.accent)
                                    Text(difference.ours)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            Text(difference.example).font(.caption.monospaced()).foregroundStyle(.secondary)
                            Picker("Decision", selection: Binding(
                                get: { difference.decision },
                                set: { lines[difference.decisionLine] = "- Decision: \($0)"; dirty = true }
                            )) {
                                ForEach(Self.decisions, id: \.self) { Text($0).tag($0) }
                            }
                            .pickerStyle(.segmented)
                        }
                    }
                }
            }
            .padding(20)
        }
        .task { load() }
    }

    private func load() {
        lines = ((try? String(contentsOf: url, encoding: .utf8)) ?? "").components(separatedBy: "\n").filter { _ in true }
        if lines == [""] { lines = [] }
        dirty = false
    }

    private func save() {
        try? lines.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
        dirty = false
    }
}
