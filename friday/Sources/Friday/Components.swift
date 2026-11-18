import SwiftUI

/// Per-Mac preferences for new tickets, the voice and the look. They live in UserDefaults, not in cw's config.
enum Preference {
    static let planFirstByDefault = "planFirstByDefault"
    static let speakReplies = "speakReplies"
    static let voiceLanguage = "voiceLanguage"
    static let accent = "accent"

    static let defaultVoiceLanguage = "en-US"
    static let defaultAccent = "blue"
}

private struct SnapshotKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// True while `--snapshot` draws the window offscreen, where translucent sidebar and inspector panes do not draw.
    var isSnapshot: Bool {
        get { self[SnapshotKey.self] }
        set { self[SnapshotKey.self] = newValue }
    }
}

enum Theme {
    static let accents: [String: Color] = [
        "blue": .blue,
        "cyan": .cyan,
        "green": .green,
        "orange": .orange,
        "purple": .purple,
    ]
    static var accent: Color {
        let chosen = UserDefaults.standard.string(forKey: Preference.accent) ?? Preference.defaultAccent
        return accents[chosen] ?? .blue
    }
    static let card = Color.primary.opacity(0.035)
    static let border = Color.primary.opacity(0.09)

    static func color(for status: TicketStatus) -> Color {
        switch status {
        case .needsInput, .awaitingApproval: .orange
        case .inProgress, .inReview: .blue
        case .ready: .teal
        case .blocked: .red
        case .paused: .purple
        case .prOpen: .green
        case .done, .abandoned: .secondary
        }
    }

    static func color(for role: AgentRole) -> Color {
        switch role {
        case .planner: .purple
        case .worker: .blue
        case .reviewer: .orange
        case .onboarder: .teal
        }
    }

    static func symbol(for role: AgentRole) -> String {
        switch role {
        case .planner: "map"
        case .worker: "hammer"
        case .reviewer: "eye"
        case .onboarder: "binoculars"
        }
    }

    static func symbol(for status: TicketStatus) -> String {
        switch status {
        case .needsInput: "questionmark.bubble"
        case .awaitingApproval: "checkmark.seal"
        case .inProgress, .inReview: "gearshape.2"
        case .ready: "tray.full"
        case .blocked: "exclamationmark.octagon"
        case .paused: "pause.circle"
        case .prOpen: "arrow.triangle.pull"
        case .done: "checkmark.circle"
        case .abandoned: "xmark.circle"
        }
    }
}

struct StatusBadge: View {
    var status: TicketStatus
    var running = false

    var body: some View {
        Label {
            Text(running ? "Running" : status.label)
        } icon: {
            if running { ProgressView().controlSize(.mini) } else { Image(systemName: Theme.symbol(for: status)) }
        }
        .font(.caption.weight(.medium))
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background((running ? Color.blue : Theme.color(for: status)).opacity(0.14), in: Capsule())
        .foregroundStyle(running ? Color.blue : Theme.color(for: status))
    }
}

struct Pill: View {
    var text: String
    var color: Color = .secondary

    var body: some View {
        Text(text)
            .font(.caption.weight(.medium))
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(color.opacity(0.13), in: Capsule())
            .foregroundStyle(color)
    }
}

struct MetricTile: View {
    var title: String
    var value: String
    var systemImage: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(title, systemImage: systemImage).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title2.weight(.semibold)).monospacedDigit()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 10))
    }
}

/// Text written in markdown by an agent: a plan, a summary, a review.
struct MarkdownText: View {
    var text: String

    var body: some View {
        Text(attributed).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
    }

    private var attributed: AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        let bulleted = text.split(separator: "\n", omittingEmptySubsequences: false)
            .map { $0.hasPrefix("- ") ? "• " + $0.dropFirst(2) : String($0) }
            .joined(separator: "\n")
        return (try? AttributedString(markdown: bulleted, options: options)) ?? AttributedString(bulleted)
    }
}

/// "3 minutes ago" from one of cw's timestamps.
struct RelativeTime: View {
    var timestamp: String

    var body: some View {
        if let date = parseDate(timestamp) {
            Text(date, format: .relative(presentation: .named))
        } else {
            Text(timestamp.shortTimestamp)
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
