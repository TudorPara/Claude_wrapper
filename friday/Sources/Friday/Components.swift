import SwiftUI

enum Theme {
    static let accent = Color(red: 0.25, green: 0.78, blue: 0.95)
    static let background = Color(red: 0.04, green: 0.05, blue: 0.07)
    static let panel = Color(red: 0.07, green: 0.08, blue: 0.11)
    static let editor = Color(red: 0.09, green: 0.10, blue: 0.13)
    static let card = Color.primary.opacity(0.04)
    static let border = Color.primary.opacity(0.08)

    static func color(for status: TicketStatus) -> Color {
        switch status {
        case .needsInput, .awaitingApproval: .orange
        case .inProgress, .inReview: accent
        case .ready: .yellow
        case .blocked: .red
        case .paused: .purple
        case .prOpen: .green
        case .done: .gray
        case .abandoned: .gray.opacity(0.6)
        }
    }
}

struct StatusChip: View {
    var status: TicketStatus
    var running = false

    var body: some View {
        HStack(spacing: 4) {
            if running { ProgressView().controlSize(.mini) }
            Text(status.label)
        }
        .font(.caption.weight(.semibold))
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(Theme.color(for: status).opacity(0.18), in: Capsule())
        .foregroundStyle(Theme.color(for: status))
    }
}

/// The pipeline chips: where a ticket is between plan and done.
struct PipelineView: View {
    var status: TicketStatus
    var route: String

    private let stages = ["Plan", "Approve", "Work", "Review", "PR", "Done"]

    private var current: Int {
        switch status {
        case .needsInput: 0
        case .awaitingApproval: 1
        case .inProgress, .blocked, .paused: 2
        case .ready, .inReview: 3
        case .prOpen: 4
        case .done, .abandoned: 5
        }
    }

    var body: some View {
        HStack(spacing: 6) {
            ForEach(Array(stages.enumerated()), id: \.offset) { index, stage in
                let skipped = route == "direct" && index < 2
                let style: Color = index < current ? .green : index == current ? Theme.color(for: status) : .secondary
                Text(stage)
                    .font(.caption.weight(index == current ? .bold : .regular))
                    .strikethrough(skipped)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(style.opacity(index <= current && !skipped ? 0.16 : 0.05), in: Capsule())
                    .overlay(Capsule().stroke(style.opacity(index == current ? 0.8 : 0.2)))
                    .foregroundStyle(skipped ? .secondary : style)
                    .help(skipped ? "Skipped: this ticket went straight to a worker" : stage)
            }
        }
    }
}

struct Card<Content: View>: View {
    var title: String
    var systemImage: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage).font(.headline)
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.border))
    }
}

/// Edits a list of strings as one item per line.
struct LinesEditor: View {
    var title: String
    var help: String
    @Binding var lines: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.subheadline.weight(.medium))
            TextEditor(
                text: Binding(
                    get: { lines.joined(separator: "\n") },
                    set: { lines = $0.split(separator: "\n").map { String($0).trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty } }
                )
            )
            .font(.system(.body, design: .monospaced))
            .frame(minHeight: 60, maxHeight: 110)
            .scrollContentBackground(.hidden)
            .padding(6)
            .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 6))
            Text(help).font(.caption).foregroundStyle(.secondary)
        }
    }
}

/// Edits a dictionary as KEY=value lines.
struct KeyValueEditor: View {
    var title: String
    var help: String
    @Binding var values: [String: String]

    var body: some View {
        LinesEditor(
            title: title,
            help: help,
            lines: Binding(
                get: { values.keys.sorted().map { "\($0)=\(values[$0] ?? "")" } },
                set: { lines in
                    var result: [String: String] = [:]
                    for line in lines {
                        let parts = line.split(separator: "=", maxSplits: 1).map(String.init)
                        if parts.count == 2 { result[parts[0]] = parts[1] }
                    }
                    values = result
                }
            )
        )
    }
}

struct LabeledStepper: View {
    var title: String
    var help: String
    @Binding var value: Int
    var range: ClosedRange<Int>
    var step = 1

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                Text(help).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            TextField("", value: $value, format: .number)
                .frame(width: 70)
                .multilineTextAlignment(.trailing)
            Stepper("", value: $value, in: range, step: step).labelsHidden()
        }
    }
}

struct EmptyHint: View {
    var title: String
    var message: String
    var systemImage: String

    var body: some View {
        ContentUnavailableView(title, systemImage: systemImage, description: Text(message))
    }
}

extension String {
    /// "2026-11-11T10:12:34.123Z" → "2026-11-11 10:12".
    var shortTimestamp: String {
        let cleaned = replacingOccurrences(of: "T", with: " ")
        return String(cleaned.prefix(16))
    }
}

extension Int {
    var compact: String {
        switch self {
        case 1_000_000...: String(format: "%.1fM", Double(self) / 1_000_000)
        case 1_000...: String(format: "%.1fk", Double(self) / 1_000)
        default: "\(self)"
        }
    }
}
