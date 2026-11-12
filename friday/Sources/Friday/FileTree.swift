import AppKit
import SwiftUI

private let hiddenNames: Set<String> = [
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".build", "target", ".idea", ".ruff_cache",
    ".pytest_cache", ".mypy_cache", ".DS_Store", "dist", "build", ".gradle",
]

struct FileNode: Identifiable, Hashable {
    let url: URL
    let isDirectory: Bool
    var id: String { url.path }
    var name: String { url.lastPathComponent }

    static func children(of url: URL) -> [FileNode] {
        let items = (try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: [.isDirectoryKey])) ?? []
        return items
            .filter { !hiddenNames.contains($0.lastPathComponent) }
            .map { FileNode(url: $0, isDirectory: (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false) }
            .sorted { ($0.isDirectory ? 0 : 1, $0.name.lowercased()) < ($1.isDirectory ? 0 : 1, $1.name.lowercased()) }
    }
}

/// Files changed in a checkout, from `git status --porcelain`, relative to its root.
func changedFiles(in root: String) -> [String: String] {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
    process.arguments = ["-C", root, "status", "--porcelain", "--untracked-files=all"]
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = FileHandle.nullDevice
    guard (try? process.run()) != nil else { return [:] }
    let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    process.waitUntilExit()
    var result: [String: String] = [:]
    for line in output.split(separator: "\n") where line.count > 3 {
        let path = String(line.dropFirst(3)).components(separatedBy: " -> ").last ?? ""
        result[path] = line.prefix(2).trimmingCharacters(in: .whitespaces)
    }
    return result
}

/// An IDE-style tree of a project folder; directories load when expanded.
struct FileTreeView: View {
    var root: String
    @Binding var selection: String?
    @State private var changes: [String: String] = [:]

    var body: some View {
        List(selection: $selection) {
            ForEach(FileNode.children(of: URL(fileURLWithPath: root)), id: \.self) { node in
                FileRow(node: node, root: root, changes: changes)
            }
        }
        .listStyle(.sidebar)
        .task(id: root) {
            while !Task.isCancelled {
                changes = changedFiles(in: root)
                try? await Task.sleep(for: .seconds(4))
            }
        }
    }
}

private struct FileRow: View {
    var node: FileNode
    var root: String
    var changes: [String: String]
    @State private var expanded = false

    private var relative: String { String(node.url.path.dropFirst(root.count + 1)) }
    private var change: String? {
        if let direct = changes[relative] { return direct }
        if node.isDirectory, changes.keys.contains(where: { $0.hasPrefix(relative + "/") }) { return "•" }
        return nil
    }

    var body: some View {
        if node.isDirectory {
            DisclosureGroup(isExpanded: $expanded) {
                if expanded {
                    ForEach(FileNode.children(of: node.url), id: \.self) { child in
                        FileRow(node: child, root: root, changes: changes)
                    }
                }
            } label: {
                label(icon: "folder.fill", color: Theme.accent.opacity(0.8))
            }
        } else {
            label(icon: "doc.text", color: .secondary).tag(node.url.path)
        }
    }

    private func label(icon: String, color: Color) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).foregroundStyle(color).font(.caption)
            Text(node.name)
                .font(.system(.callout, design: .monospaced))
                .foregroundStyle(change == nil ? Color.primary : changeColor)
                .lineLimit(1)
            Spacer()
            if let change { Text(change).font(.caption2.monospaced()).foregroundStyle(changeColor) }
        }
    }

    private var changeColor: Color {
        switch change {
        case "??", "A": .green
        case "D": .red
        default: .orange
        }
    }
}

/// A read-only, fast code viewer.
struct CodeView: NSViewRepresentable {
    var path: String

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        let text = scroll.documentView as! NSTextView
        text.isEditable = false
        text.isSelectable = true
        text.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        text.backgroundColor = NSColor(Theme.editor)
        text.textContainerInset = NSSize(width: 12, height: 12)
        scroll.drawsBackground = false
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let text = scroll.documentView as? NSTextView else { return }
        let data = FileManager.default.contents(atPath: path) ?? Data()
        let content: String
        if data.count > 2_000_000 {
            content = "File too large to preview (\(data.count / 1024) KB)."
        } else {
            content = String(data: data, encoding: .utf8) ?? "Binary file."
        }
        let numbered = content.split(separator: "\n", omittingEmptySubsequences: false).enumerated()
            .map { String(format: "%4d  ", $0.offset + 1) + $0.element }
            .joined(separator: "\n")
        if text.string != numbered {
            text.string = numbered
            text.textColor = NSColor.labelColor
        }
    }
}
